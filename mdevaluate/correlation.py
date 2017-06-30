import numpy as np
import numba
from scipy.special import legendre
from itertools import chain
import dask.array as darray

from .meta import annotate
from .autosave import autosave_data
from .utils import filon_fourier_transformation, coherent_sum, histogram
from .pbc import pbc_diff
from .logging import logger


def log_indices(first, last, num=100):
    ls = np.logspace(0, np.log10(last - first + 1), num=num)
    return np.unique(np.int_(ls) - 1 + first)


def correlation(function, frames):
    iterator = iter(frames)
    start_frame = next(iterator)
    return map(lambda f: function(start_frame, f), chain([start_frame], iterator))


def subensemble_correlation(selector_function, correlation_function=correlation):

    def c(function, frames):
        iterator = iter(frames)
        start_frame = next(iterator)
        selector = selector_function(start_frame)
        subensemble = map(lambda f: f[selector], chain([start_frame], iterator))
        return correlation_function(function, subensemble)
    return c


@autosave_data(nargs=2, kwargs_keys=(
    'index_distribution', 'correlation', 'segments', 'window', 'skip', 'average'
))
def shifted_correlation(function, frames,
                        index_distribution=log_indices, correlation=correlation,
                        segments=10, window=0.5, skip=None,
                        average=False,):
    """
    Calculate the time series for a correlation function

    The times at which the correlation is calculated are determined automatically by the
    function given as ``index_distribution``. The default is a logarithmic distribution.

    Args:
        function:   The function that should be correlated
        frames:     The coordinates of the simulation data
        index_distribution (opt.):
                    A function that returns the indices for which the timeseries
                    will be calculated
        correlation (function, opt.):
                    The correlation function
        segments (int, opt.):
                    The number of segments the time window will be shifted
        window (float, opt.):
                    The fraction of the simulation the time series will cover
        skip (float, opt.):
                    The fraction of the trajectory that will be skipped at the beginning,
                    if this is None the start index of the frames slice will be used,
                    which defaults to 0.
    Returns:
        tuple:
            A list of length N that contains the indices of the frames at which
            the time series was calculated and a numpy array of shape (segments, N)
            that holds the (non-avaraged) correlation data

    Example:
        Calculating the mean square displacement of a coordinates object named ``coords``:

        >>> indices, data = shifted_correlation(msd, coords)
    """
    if skip is None:
        skip = frames._slice.start / len(frames) if hasattr(frames, '_slice') else 0
    assert window + skip < 1

    start_frames = np.linspace(
        len(frames) * skip, len(frames) * (1 - window - skip),
        num=segments, endpoint=False, dtype=int
    )
    num_frames = int(len(frames) * (window))

    idx = index_distribution(0, num_frames)

    def correlate(start_frame):
        shifted_idx = idx + start_frame
        return correlation(function, map(frames.__getitem__, shifted_idx))

    result = []
    for i, start_frame in enumerate(start_frames):
        logger.debug('shifted_correlation: segment {}/{} (index={})'.format(i + 1, segments, start_frame))
        result.append(list(correlate(start_frame)))
    result = np.array(result)
    if average:
        result = result.mean(axis=0)
    times = np.array([frames[i].time for i in idx]) - frames[0].time
    return times, result


def msd(start, frame):
    """
    Mean square displacement
    """
    vec = start - frame
    return (vec ** 2).sum(axis=1).mean()


def isf(start, frame, q, box=None):
    """
    Incoherent intermediate scattering function. To specify q, use
    water_isf = functools.partial(isf, q=22.77) # q has the value 22.77 nm^-1

    :param q: length of scattering vector
    """
    vec = start - frame
    distance = (vec ** 2).sum(axis=1) ** .5
    return np.sinc(distance * q / np.pi).mean()


def rotational_autocorrelation(onset, frame, order=2):
    """
    Compute the rotaional autocorrelation of the legendre polynamial for the given vectors.

    Args:
        onset, frame: CoordinateFrames of vectors
        order (opt.): Order of the legendre polynomial.

    Returns:
        Skalar value of the correltaion function.
    """
    scalar_prod = (onset * frame).sum(axis=-1)
    poly = legendre(order)
    return poly(scalar_prod).mean()


@annotate.untested
def oaf(start, frame):
    """
    Orientation autocorrelation function. start and frame must be connection vectors, not absolute coordinates. Use for
    example oaf_indexed to define connection vectors.

    :param start:
    :param frame:
    :return:
    """
    vec_start_norm = np.norm(start)
    vec_frame_norm = np.norm(frame)

    dot_prod = (start * frame).sum(axis=1) / (vec_start_norm, vec_frame_norm)
    return (3 * dot_prod**2 - 1).mean() / 2.0


def oaf_indexed(index_from, index_to):
    """
    Returns a OAF correlation function. Example
    oaf_indexed(t[:,1] == 'C', t[:,1] == 'O')
    :param index_from:
    :param index_to:
    :return:
    """
    return lambda start, frame: oaf(start[index_to] - start[index_from],
                                    frame[index_to] - frame[index_from])


def van_hove_self(start, end, bins):
    """
    Compute the self part of the Van Hove autocorrelation function.

    ..math::
      G(r, t) = \sum_i \delta(|\vec r_i(0) - \vec r_i(t)| - r)
    """
    vec = start - end
    delta_r = ((vec)**2).sum(axis=-1)**.5
    return 1 / len(start) * np.histogram(delta_r, bins)[0]


def van_hove_distinct(onset, frame, bins, box=None):
    """
    Compute the distinct part of the Van Hove autocorrelation function.

    ..math::
      G(r, t) = \sum_{i, j} \delta(|\vec r_i(0) - \vec r_j(t)| - r)
    """
    if box is None:
        box = onset.box.diagonal()
    dimension = len(box)
    N = len(onset)
    onset = darray.from_array(onset, chunks=(500, dimension)).reshape(1, N, dimension)
    frame = darray.from_array(frame, chunks=(500, dimension)).reshape(N, 1, dimension)
    dist = (pbc_diff(onset, frame, box)**2).sum(axis=-1)**0.5
    hist = darray.histogram(dist, bins=bins)[0]
    return hist.compute() / N


def overlap(onset, frame, crds_tree, radius):
    """
    Compute the overlap with a reference configuration defined in a CoordinatesTree.

    Args:
        onset: Initial frame, this is only used to get the frame index
        frame: The current configuration
        crds_tree: A CoordinatesTree of the reference configurations
        radius: The cutoff radius for the overlap

    This function is intended to be used with :func:`shifted_correlation`.
    As usual the first two arguments are used internally and the remaining ones
    should be defined with :func:`functools.partial`.

    If the overlap of a subset of the system should be calculated, this has to be
    defined through a selection of the reference configurations in the CoordinatesTree.

    Example:
        >>> shifted_correlation(
        ...     partial(overlap, crds_tree=CoordinatesTree(traj), radius=0.11),
        ...     traj
        ... )
    """
    tree = crds_tree[onset.step]
    return (tree.query(frame)[0] <= radius).sum() / tree.n


def susceptibility(time, correlation, **kwargs):
    """
    Calculate the susceptibility of a correlation function.

    Args:
        time: Timesteps of the correlation data
        correlation: Value of the correlation function
        **kwargs (opt.):
            Additional keyword arguments will be passed to :func:`filon_fourier_transformation`.
    """
    frequencies, fourier = filon_fourier_transformation(time, correlation, imag=False, **kwargs)
    return frequencies, frequencies * fourier


def coherent_scattering_function(onset, frame, q):
    """
    Calculate the coherent scattering function.
    """
    box = onset.box.diagonal()
    dimension = len(box)

    @numba.jit(nopython=True)
    def scfunc(x, y):
        sqdist = 0
        for i in range(dimension):
            d = x[i] - y[i]
            if d > box[i] / 2:
                d -= box[i]
            if d < -box[i] / 2:
                d += box[i]
            sqdist += d**2
        x = sqdist**0.5 * q
        if x == 0:
            return 1.0
        else:
            return np.sin(x) / x

    return coherent_sum(scfunc, onset.pbc, frame.pbc) / len(onset)


def non_gaussian(onset, frame):
    """
    Calculate the Non-Gaussian parameter :
    ..math:
      \alpha_2 (t) = \frac{3}{5}\frac{\langle r_i^4(t)\rangle}{\langle r_i^2(t)\rangle^2} - 1
    """
    r_2 = ((frame - onset)**2).sum(axis=-1)
    return 3 / 5 * (r_2**2).mean() / r_2.mean()**2 - 1
