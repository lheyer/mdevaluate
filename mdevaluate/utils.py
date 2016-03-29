"""
Collection of utility functions.
"""
import numpy as np


def hash_anything(arg):
    """Return a hash value for the current state of any argument."""
    try:
        return hash(arg)
    except TypeError:
        s = ''
        if isinstance(arg, np.ndarray):
            s = arg.tostring()
        else:
            s = str(arg)
        return hash(s)


def merge_hashes(*hashes):
    """Merge several hashes to one hash value."""
    return hash(''.join([str(h) for h in hashes]))


def five_point_stencil(xdata, ydata):
    """
    Calculate the derivative dy/dx with a five point stencil.
    This algorith is only valid for equally distributed x values.

    Args:
        xdata: x values of the data points
        ydata: y values of the data points

    Returns:
        Values where the derivative was estimated and the value of the derivative at these points.

    See: https://en.wikipedia.org/wiki/Five-point_stencil
    """
    return xdata[1:-1], (
        (-ydata[3:] + 8 * ydata[2:-1] - 8 * ydata[1:-2] + ydata[:-3]) /
        (12 * (xdata[2:-1] - xdata[1:-2]))
        )


def filon_fourier_transformation(time, correlation,
                                 frequencies=None, derivative='linear', imag=True,
                                 ):
    """
    Fourier-transformation for slow varrying functions. The filon algorithmus is
    described in detail in [1].

    Args:
        time: List of times where the correlation function was sampled.
        correlation: Values of the correlation function.
        frequencies (opt.):
            List of frequencies where the fourier transformation will be calculated.
            If None the frequencies will be choosen based on the input times.
        derivative (opt.):
            Approximation algorithmus for the derivative of the correlation function.
            Possible values are: 'linear', 'stencil' or a list of derivatives.
        imag (opt.): If imaginary part of the integral should be calculated.



    Reference:
        [1] T. Blochowicz, Broadband dielectric spectroscopy in neat and binary
        molecular glass formers, Ph.D. thesis, Uni-versität Bayreuth (2003)
    """
    if frequencies is None:
        f_min = 1 / time[time > 0][-1]
        f_max = 1 / time[time > 0][0]
        frequencies = 2*np.pi*np.logspace(
            np.log10(f_min), np.log10(f_max), num=100
        )
    frequencies.reshape(1, -1)

    if derivative is 'linear':
        derivative = (np.diff(correlation) / np.diff(time)).reshape(-1, 1)
    elif derivative is 'stencil':
        time, derivative = five_point_stencil(time, correlation)
        time = time.reshape(-1, 1)
        derivative = derivative.reshape(-1, 1)
    elif np.iterable(derivative) and len(time) is len(derivative):
        pass
    else:
        raise NotImplementedError(
            'Invalid approximation method {}. Possible values are "linear", "stencil" or "direct".'
            )
    time = time.reshape(-1, 1)

    integral = (np.cos(frequencies * time[1:]) - np.cos(frequencies * time[:-1])) / frequencies**2
    if imag:
        integral = integral + 1j * (
            correlation[0]/frequencies +
            (np.sin(frequencies * time[1:]) - np.sin(frequencies * time[:-1])) / frequencies**2
            )
    fourier = (derivative * integral).sum(axis=0) / derivative.size

    return frequencies.reshape(-1,), fourier
