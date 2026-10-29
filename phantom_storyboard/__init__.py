bl_info = {
    "name": "Phantom Storyboard",
    "author": "vvabi-sabi",
    "version": (0, 1, 4),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > Phantom",
    "description": "Curved media backdrops and bounded 3D-to-2D phantom animation",
    "category": "Animation",
}

from . import operators, props, ui


def register():
    props.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    props.unregister()
