import torch
import numpy as np
import scipy.interpolate

def project_snr(clean, perturbation, snr_db):
    """
    Ensures the perturbation has SNR ≥ snr_db (in dB) relative to clean signal.
    If SNR is already high enough, returns unchanged.
    Otherwise, rescales perturbation to match the target SNR.
    """
    signal_power = torch.mean(clean ** 2)
    noise_power = torch.mean(perturbation ** 2)

    # Current SNR in dB
    current_snr_db = 10 * torch.log10(signal_power / (noise_power + 1e-12))

    if current_snr_db >= snr_db:
        return perturbation  # Already satisfies SNR requirement

    # Otherwise, project to match target SNR
    snr_linear = 10 ** (snr_db / 10)
    target_noise_power = signal_power / snr_linear
    target_norm = torch.sqrt(target_noise_power * clean.numel())

    current_norm = torch.norm(perturbation.view(-1), p=2)
    if current_norm < 1e-8:
        return perturbation  # Nothing to scale

    return perturbation * (target_norm / current_norm)

def project_linf(p,min_val,max_val):
    return torch.clamp(p,min_val,max_val)

def project_l2(p, epsilon):
    norm = torch.norm(p, p=2)
    if norm < 1e-8:
        return p
    return p * (epsilon / norm)


def project_min_max_freqs(args,stft_p, min_freq, max_freq):

    # Get frequencies for STFT bins
    freq_bins = stft_p.shape[-2]
    freqs = torch.fft.rfftfreq(n=args.n_fft, d=1 / args.sr).to(stft_p.device)

    # Mask: 1 where freqs < min or freqs > max
    mask = ((freqs < min_freq) | (freqs > max_freq)).float()
    mask = mask.view(1, -1, 1)  # Match (batch, freq, time)

    # Apply mask
    stft_p = stft_p * mask

    # Inverse STFT to time domain
    return stft_p


def compute_fm_weighted_norm(stft_p: torch.Tensor, weights_matrix: np.ndarray) -> torch.Tensor:
    """
    Computes perceptual (FM-weighted) norm of the STFT of the perturbation,
    using a 2D perceptual weight matrix indexed by frequency and SPL bin (phon level).
    """

    B, F, T = stft_p.shape
    power = stft_p.abs() ** 2
    spl = 10 * torch.log10(power + 1e-12)  # [B, F, T]

    # Discretize SPL into phon bins (0 to 9)
    phon_bin = (spl / 10).long()
    phon_bin = torch.clamp(phon_bin, min=0, max=weights_matrix.shape[0] - 1)  # [B, F, T]

    # Frequency bin index (shared across batch and time)
    freq_bins = torch.arange(F, device=stft_p.device).view(1, F, 1).expand(B, F, T)
    freq_bins = torch.clamp(freq_bins, max=weights_matrix.shape[1] - 1)  # safety

    # Index weights
    weights_2d = torch.tensor(weights_matrix, device=stft_p.device, dtype=torch.float32)  # [P, F]
    perceptual_weights = weights_2d[phon_bin, freq_bins]  # [B, F, T]

    weighted_power = power * perceptual_weights
    return torch.sqrt(weighted_power.sum())


def project_fm_norm(stft_p, args, weights_matrix):
    """
    Projects the perturbation in the STFT domain to have a perceptual FM-weighted norm ≤ epsilon.

    Args:
        stft_p (torch.Tensor): Complex STFT of the perturbation. Shape: [B, F, T]
        args: Namespace containing args.fm_epsilon (max perceptual norm)
        weights_matrix (np.ndarray): 2D perceptual weight matrix. Shape: [P, F]

    Returns:
        torch.Tensor: Projected STFT perturbation with FM-weighted norm ≤ epsilon
    """

    norm = compute_fm_weighted_norm(stft_p, weights_matrix)
    if norm <= args.fm_epsilon:
        return stft_p
    scale = args.fm_epsilon / norm.clamp(min=1e-8)
    return stft_p * scale
