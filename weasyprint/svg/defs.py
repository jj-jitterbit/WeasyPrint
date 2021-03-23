from itertools import cycle
from math import ceil, hypot

import pydyf

from .colors import color
from .utils import parse_url, size


def linear_gradient(svg, node, font_size):
    svg.parse_def(node)


def radial_gradient(svg, node, font_size):
    svg.parse_def(node)


def marker(svg, node, font_size):
    svg.parse_def(node)


def use(svg, node, font_size):
    from . import SVG

    svg.stream.push_state()
    svg.stream.transform(
        1, 0, 0, 1, *svg.point(node.get('x'), node.get('y'), font_size))

    for attribute in ('x', 'y', 'viewBox', 'mask'):
        if attribute in node.attrib:
            del node.attrib[attribute]

    parsed_url = parse_url(node.get_href())
    if parsed_url.fragment and not parsed_url.path:
        tree = svg.tree.get_child(parsed_url.fragment)
    else:
        url = parsed_url.geturl()
        try:
            bytestring_svg = svg.url_fetcher(url)
            use_svg = SVG(bytestring_svg, url)
        except TypeError:
            svg.stream.restore()
            return
        else:
            use_svg.get_intrinsic_size(font_size)
            tree = use_svg.tree

    if tree.tag in ('svg', 'symbol'):
        # Explicitely specified
        # http://www.w3.org/TR/SVG11/struct.html#UseElement
        tree.tag = 'svg'
        if 'width' in node.attrib and 'height' in node.attrib:
            tree.attrib['width'] = node['width']
            tree.attrib['height'] = node['height']

    svg.draw_node(tree, font_size)
    svg.stream.pop_state()


def draw_gradient_or_pattern(svg, node, name, font_size, stroke):
    if name in svg.gradients:
        return draw_gradient(svg, node, svg.gradients[name], font_size, stroke)
    elif name in svg.patterns:
        # return draw_pattern(svg, node, name)
        return False


def draw_gradient(svg, node, gradient, font_size, stroke):
    positions = []
    colors = []
    for child in gradient:
        positions.append(max(
            positions[-1] if positions else 0,
            size(child.get('offset'), font_size, 1)))
        colors.append(color(child.get('stop-color', 'black')))

    if len(colors) == 1:
        red, green, blue, alpha = colors[0]
        svg.stream.set_color_rgb(red, green, blue)
        if alpha != 1:
            svg.stream.set_alpha(alpha, stroke=stroke)
        return True

    bounding_box = svg.calculate_bounding_box(node, font_size)
    if not bounding_box:
        return False
    if gradient.get('gradientUnits') == 'userSpaceOnUse':
        x, y, _, _ = bounding_box
        width, height = svg.concrete_width, svg.concrete_height
        pattern_matrix = svg.stream.ctm
    else:
        x, y, width, height = bounding_box
        pattern_matrix = svg.stream.ctm

    spread = gradient.get('spreadMethod', 'pad')
    if spread not in ('repeat', 'reflect'):
        # Add explicit colors at boundaries if needed, because PDF doesn’t
        # extend color stops that are not displayed
        if positions[0] == positions[1]:
            positions.insert(0, positions[0] - 1)
            colors.insert(0, colors[0])
        if positions[-2] == positions[-1]:
            positions.append(positions[-1] + 1)
            colors.append(colors[-1])

    if gradient.tag == 'linearGradient':
        shading_type = 2
        x1, y1 = (
            size(gradient.get('x1', 0), font_size, width),
            size(gradient.get('y1', 0), font_size, height))
        x2, y2 = (
            size(gradient.get('x2', '100%'), font_size, width),
            size(gradient.get('y2', 0), font_size, height))
        if gradient.get('gradientUnits') == 'userSpaceOnUse':
            x1 -= x
            y1 -= y
            x2 -= x
            y2 -= y
        positions, colors, coords = spread_linear_gradient(
            spread, positions, colors, x1, y1, x2, y2)
    else:
        assert gradient.tag == 'radialGradient'
        shading_type = 3
        cx, cy = (
            size(gradient.get('cx', '50%'), font_size, width),
            size(gradient.get('cy', '50%'), font_size, height))
        r = size(gradient.get('r', '50%'), font_size, hypot(width, height))
        fx, fy = (
            size(gradient.get('fx', cx), font_size, width),
            size(gradient.get('fy', cy), font_size, height))
        fr = size(gradient.get('fr', 0), font_size, hypot(width, height))
        if gradient.get('gradientUnits') == 'userSpaceOnUse':
            cx -= x
            cy -= y
            fx -= x
            fy -= y
        positions, colors, coords = spread_radial_gradient(
            spread, positions, colors, fx, fy, fr, cx, cy, r, width, height)

    alphas = [color[3] for color in colors]
    alpha_couples = [
        (alphas[i], alphas[i + 1])
        for i in range(len(alphas) - 1)]
    color_couples = [
        [colors[i][:3], colors[i + 1][:3], 1]
        for i in range(len(colors) - 1)]

    # Premultiply colors
    for i, alpha in enumerate(alphas):
        if alpha == 0:
            if i > 0:
                color_couples[i - 1][1] = color_couples[i - 1][0]
            if i < len(colors) - 1:
                color_couples[i][0] = color_couples[i][1]
    for i, (a0, a1) in enumerate(alpha_couples):
        if 0 not in (a0, a1) and (a0, a1) != (1, 1):
            color_couples[i][2] = a0 / a1

    pattern = svg.stream.add_pattern(
        0, 0, width, height, width, height, pattern_matrix)
    child = pattern.add_transparency_group([0, 0, width, height])

    shading = child.add_shading()
    shading['ShadingType'] = shading_type
    shading['ColorSpace'] = '/DeviceRGB'
    shading['Domain'] = pydyf.Array([positions[0], positions[-1]])
    shading['Coords'] = pydyf.Array(coords)
    shading['Function'] = pydyf.Dictionary({
        'FunctionType': 3,
        'Domain': pydyf.Array([positions[0], positions[-1]]),
        'Encode': pydyf.Array((len(colors) - 1) * [0, 1]),
        'Bounds': pydyf.Array(positions[1:-1]),
        'Functions': pydyf.Array([
            pydyf.Dictionary({
                'FunctionType': 2,
                'Domain': pydyf.Array([positions[0], positions[-1]]),
                'C0': pydyf.Array(c0),
                'C1': pydyf.Array(c1),
                'N': n,
            }) for c0, c1, n in color_couples
        ]),
    })
    if spread not in ('repeat', 'reflect'):
        shading['Extend'] = pydyf.Array([b'true', b'true'])

    if any(alpha != 1 for alpha in alphas):
        alpha_stream = child.add_transparency_group(
            [0, 0, svg.concrete_width, svg.concrete_height])
        alpha_state = pydyf.Dictionary({
            'Type': '/ExtGState',
            'SMask': pydyf.Dictionary({
                'Type': '/Mask',
                'S': '/Luminosity',
                'G': alpha_stream,
            }),
            'ca': 1,
            'AIS': 'false',
        })
        alpha_state_id = f'as{len(child._alpha_states)}'
        child._alpha_states[alpha_state_id] = alpha_state
        child.set_state(alpha_state_id)

        alpha_shading = alpha_stream.add_shading()
        alpha_shading['ShadingType'] = shading_type
        alpha_shading['ColorSpace'] = '/DeviceGray'
        alpha_shading['Domain'] = pydyf.Array(
            [positions[0], positions[-1]])
        alpha_shading['Coords'] = pydyf.Array(coords)
        alpha_shading['Function'] = pydyf.Dictionary({
            'FunctionType': 3,
            'Domain': pydyf.Array([positions[0], positions[-1]]),
            'Encode': pydyf.Array((len(colors) - 1) * [0, 1]),
            'Bounds': pydyf.Array(positions[1:-1]),
            'Functions': pydyf.Array([
                pydyf.Dictionary({
                    'FunctionType': 2,
                    'Domain': pydyf.Array([0, 1]),
                    'C0': pydyf.Array([c0]),
                    'C1': pydyf.Array([c1]),
                    'N': 1,
                }) for c0, c1 in alpha_couples
            ]),
        })
        if spread not in ('repeat', 'reflect'):
            alpha_shading['Extend'] = pydyf.Array([b'true', b'true'])
        alpha_stream.stream = [f'/{alpha_shading.id} sh']

    child.shading(shading.id)

    pattern.draw_x_object(child.id)
    svg.stream.color_space('Pattern', stroke=stroke)
    svg.stream.set_color_special(pattern.id, stroke=stroke)
    return True


def spread_linear_gradient(spread, positions, colors, x1, y1, x2, y2):
    from ..images import gradient_average_color, normalize_stop_positions

    first, last, positions = normalize_stop_positions(positions)
    if spread in ('repeat', 'reflect'):
        # Render as a solid color if the first and last positions are equal
        # See https://drafts.csswg.org/css-images-3/#repeating-gradients
        if first == last:
            average_color = gradient_average_color(colors, positions)
            return 1, 'solid', None, [], [average_color]

        # Define defined gradient length and steps between positions
        stop_length = last - first
        assert stop_length > 0
        position_steps = [
            positions[i + 1] - positions[i]
            for i in range(len(positions) - 1)]

        # Create cycles used to add colors
        if spread == 'repeat':
            next_steps = cycle([0] + position_steps)
            next_colors = cycle(colors)
            previous_steps = cycle([0] + position_steps[::-1])
            previous_colors = cycle(colors[::-1])
        else:
            assert spread == 'reflect'
            next_steps = cycle(
                [0] + position_steps[::-1] + [0] + position_steps)
            next_colors = cycle(colors[::-1] + colors)
            previous_steps = cycle(
                [0] + position_steps + [0] + position_steps[::-1])
            previous_colors = cycle(colors + colors[::-1])

        # Add colors after last step
        while last < hypot(x2 - x1, y2 - y1):
            step = next(next_steps)
            colors.append(next(next_colors))
            positions.append(positions[-1] + step)
            last += step * stop_length

        # Add colors before last step
        while first > 0:
            step = next(previous_steps)
            colors.insert(0, next(previous_colors))
            positions.insert(0, positions[0] - step)
            first -= step * stop_length

    x1, x2 = x1 + (x2 - x1) * first, x1 + (x2 - x1) * last
    y1, y2 = y1 + (y2 - y1) * first, y1 + (y2 - y1) * last
    coords = (x1, y1, x2, y2)
    return positions, colors, coords


def spread_radial_gradient(spread, positions, colors, fx, fy, fr, cx, cy, r,
                           width, height):
    from ..images import gradient_average_color, normalize_stop_positions

    first, last, positions = normalize_stop_positions(positions)
    fr, r = fr + (r - fr) * first, fr + (r - fr) * last

    if spread in ('repeat', 'reflect'):
        # Keep original lists and values, they’re useful
        original_colors = colors.copy()
        original_positions = positions.copy()
        gradient_length = r - fr

        # Get the maximum distance between the center and the corners, to find
        # how many times we have to repeat the colors outside
        max_distance = max(
            hypot(width - fx, height - fy),
            hypot(width - fx, -fy),
            hypot(-fx, height - fy),
            hypot(-fx, -fy))
        repeat_after = ceil((max_distance - r) / gradient_length)
        if repeat_after > 0:
            # Repeat colors and extrapolate positions
            repeat = 1 + repeat_after
            if spread == 'repeat':
                colors *= repeat
            else:
                assert spread == 'reflect'
                colors = []
                for i in range(repeat):
                    colors += original_colors[::1 if i % 2 else -1]
            positions = [
                i + position for i in range(repeat) for position in positions]
            r += gradient_length * repeat_after

        if fr == 0:
            # Inner circle has 0 radius, no need to repeat inside, return
            coords = (fx, fy, fr, cx, cy, r)
            return positions, colors, coords

        # Find how many times we have to repeat the colors inside
        repeat_before = fr / gradient_length

        # Set the inner circle size to 0
        fr = 0

        # Find how many times the whole gradient can be repeated
        full_repeat = int(repeat_before)
        if full_repeat:
            # Repeat colors and extrapolate positions
            if spread == 'repeat':
                colors += original_colors * full_repeat
            else:
                assert spread == 'reflect'
                for i in range(full_repeat):
                    colors += original_colors[::1 if i % 2 else -1]
            positions = [
                i - full_repeat + position for i in range(full_repeat)
                for position in original_positions] + positions

        # Find the ratio of gradient that must be added to reach the center
        partial_repeat = repeat_before - full_repeat
        if partial_repeat == 0:
            # No partial repeat, return
            coords = (fx, fy, fr, cx, cy, r)
            return positions, colors, coords

        # Iterate through positions in reverse order, from the outer
        # circle to the original inner circle, to find positions from
        # the inner circle (including full repeats) to the center
        assert (original_positions[0], original_positions[-1]) == (0, 1)
        assert 0 < partial_repeat < 1
        reverse = original_positions[::-1]
        ratio = 1 - partial_repeat
        for i, position in enumerate(reverse, start=1):
            if position == ratio:
                # The center is a color of the gradient, truncate original
                # colors and positions and prepend them
                colors = original_colors[-i:] + colors
                new_positions = [
                    position - full_repeat - 1
                    for position in original_positions[-i:]]
                positions = new_positions + positions
                coords = (fx, fy, fr, cx, cy, r)
                return positions, colors, coords
            if position < ratio:
                # The center is between two colors of the gradient,
                # define the center color as the average of these two
                # gradient colors
                color = original_colors[-i]
                next_color = original_colors[-(i - 1)]
                next_position = original_positions[-(i - 1)]
                average_colors = [color, color, next_color, next_color]
                average_positions = [position, ratio, ratio, next_position]
                zero_color = gradient_average_color(
                    average_colors, average_positions)
                colors = [zero_color] + original_colors[-(i - 1):] + colors
                new_positions = [
                    position - 1 - full_repeat for position
                    in original_positions[-(i - 1):]]
                positions = (
                    [ratio - 1 - full_repeat] + new_positions + positions)

    coords = (fx, fy, fr, cx, cy, r)
    return positions, colors, coords