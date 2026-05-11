from dataclasses import dataclass
from typing import Sequence

import numpy as np
from maya.api.OpenMaya import MColor, MColorArray
from numpy.typing import NDArray

from .color import (
    blend_colors_by_weight,
    lch_to_lab,
    linear_srgb_to_rec2020,
    oklab_to_linear_srgb,
)
from .spline import point_on_spline_weights


@dataclass
class GradientStop:
    position: float
    color: tuple[float, float, float]


@dataclass
class Gradient:
    stops: tuple[GradientStop, ...]
    degree: int


OKLCH_HEATMAP_GRADIENT = Gradient(
    stops=(
        GradientStop(0.0, (0, 0.05, -80)),
        GradientStop(0.01, (0.1, 0.15, -60)),
        GradientStop(0.2, (0.7, 0.15, 0)),
        GradientStop(1.0, (1.0, 0.15, 90)),
    ),
    degree=2,
)


def blend_mcolors_by_weight(color_weights: Sequence[tuple[MColor, float]]) -> MColor:
    final_color: MColor = MColor((0.0, 0.0, 0.0))
    for color, weight in color_weights:
        final_color += color * weight
    return final_color


def oklch_to_linear_srgb(color: MColor) -> MColor:
    return MColor(
        linear_srgb_to_rec2020(oklab_to_linear_srgb(lch_to_lab(color.getColor())))
    )


def get_gradient_knots(gradient: Gradient):
    degree = gradient.degree
    stop_positions = [stop.position for stop in gradient.stops]
    clamp_start = [stop_positions[0]] * (degree + 1)
    clamp_end = [stop_positions[-1]] * (degree + 1)

    num_internal = len(gradient.stops) - degree - 1
    internal_knots = list(
        (sum(stop_positions[j : j + degree]) / degree)
        for j in range(1, num_internal + 1)
    )
    clamped_knots = clamp_start + internal_knots + clamp_end
    return [float(knot) for knot in clamped_knots]


def sample_spline_gradient(
    gradient: Gradient, position: float
) -> tuple[float, float, float]:
    gradient_stop_colors = [stop.color for stop in gradient.stops]
    gradient_knots = get_gradient_knots(gradient)
    color_weights = point_on_spline_weights(
        cvs=gradient_stop_colors,
        t=position,
        knots=gradient_knots,
        degree=gradient.degree,
        normalize=False,
    )
    return blend_colors_by_weight(
        colors=(tuple(color_weight[0] for color_weight in color_weights)),
        weights=(tuple(color_weight[1] for color_weight in color_weights)),
    )


def numpy_array_to_colors(array: NDArray[np.float64]) -> MColorArray:
    color_array = MColorArray()
    color_array.setLength(len(array))
    for i, color_row in enumerate(array):
        color_array[i] = MColor(list(color_row))
    return color_array


def fast_sample_lch_gradient_as_linear_srgb(
    positions: Sequence[float],
    gradient: Gradient = OKLCH_HEATMAP_GRADIENT,
    sample_points: int = 128,
) -> MColorArray:
    result: MColorArray = MColorArray()
    result.setLength(len(positions))
    gradient_stop_colors = [MColor(stop.color) for stop in gradient.stops]
    gradient_knots = get_gradient_knots(gradient)

    if len(positions) <= sample_points:
        for index, position in enumerate(positions):
            weights = point_on_spline_weights(
                cvs=gradient_stop_colors,
                t=position,
                degree=gradient.degree,
                knots=gradient_knots,
                normalize=False,
            )
            result[index] = oklch_to_linear_srgb(blend_mcolors_by_weight(weights))
        return result

    # Precompute lookup table
    parameter_array = np.array(positions, dtype=np.float64)
    min_t, max_t = min(positions), max(positions)
    t_range: float = max_t - min_t
    if t_range == 0:
        # All parameters are the same, just calculate the one weight
        weights = point_on_spline_weights(
            cvs=gradient_stop_colors,
            t=min_t,
            degree=gradient.degree,
            knots=gradient_knots,
            normalize=False,
        )
        for index, _ in enumerate(positions):
            result[index] = oklch_to_linear_srgb(blend_mcolors_by_weight(weights))
        return result

    # Get evenly spaced points from the minimum to maximum t value
    sample_params = np.linspace(min_t, max_t, sample_points, dtype=float)
    lut_colors = np.zeros((sample_points, 4), dtype=float)

    for sample_index, sample_parameter in enumerate(sample_params):
        weights = point_on_spline_weights(
            cvs=gradient_stop_colors,
            t=sample_parameter,
            degree=gradient.degree,
            knots=gradient_knots,
            normalize=False,
        )
        color = oklch_to_linear_srgb(blend_mcolors_by_weight(weights))
        # Take the weights and put them into the correct row in the array
        lut_colors[sample_index, :] = color.getColor()

    # Map each parameter to LUT index positions
    normalized_positions = (parameter_array - min_t) / t_range * (sample_points - 1)
    lower_indices = np.floor(normalized_positions).astype(int)
    upper_indices = np.clip(lower_indices + 1, 0, sample_points - 1)
    interpolation_alphas = (normalized_positions - lower_indices)[:, None]

    # Interpolate weights for all parameters in bulk
    interpolated_color_array = (1.0 - interpolation_alphas) * lut_colors[
        lower_indices, :
    ] + interpolation_alphas * lut_colors[upper_indices, :]

    # Reattach CV references to each interpolated weight row
    return numpy_array_to_colors(interpolated_color_array)
