from math import sqrt


def held_frame_groups(frame_start, frame_end, step):
    if frame_end < frame_start or step < 1:
        raise ValueError("Invalid frame range or timing step")
    for frame in range(frame_start, frame_end + 1, step):
        yield frame, range(frame, min(frame + step, frame_end + 1))


def curved_screen_geometry(width, height, curvature, segments):
    if width <= 0 or height <= 0:
        raise ValueError("Screen width and height must be positive")
    if segments < 2:
        raise ValueError("Screen needs at least two segments")

    vertices = []
    uvs = []
    for index in range(segments + 1):
        t = index / segments
        normalized_x = 2.0 * t - 1.0
        x = (t - 0.5) * width
        y = -curvature * (1.0 - sqrt(max(0.0, 1.0 - normalized_x * normalized_x)))
        vertices.extend(((x, y, -height / 2.0), (x, y, height / 2.0)))
        uvs.extend(((t, 0.0), (t, 1.0)))

    faces = [
        (2 * index, 2 * index + 2, 2 * index + 3, 2 * index + 1)
        for index in range(segments)
    ]
    return vertices, faces, uvs


def unit_cube_geometry():
    vertices = [
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (4, 0, 3, 7),
    ]
    return vertices, faces
