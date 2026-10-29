import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
)


def _update_box_visibility(self, _context):
    for obj in bpy.data.objects:
        if obj.get("phantom_kind") == "CAGE":
            obj.hide_viewport = not self.is_box_visible


class PhantomSettings(bpy.types.PropertyGroup):
    phantom_collection: PointerProperty(type=bpy.types.Collection)
    bake_step: EnumProperty(
        name="Timing",
        items=(
            ("1", "On ones", "Render every frame"),
            ("2", "On twos", "Hold every image for two frames"),
            ("3", "On threes", "Hold every image for three frames"),
            ("4", "On fours", "Hold every image for four frames"),
        ),
        default="2",
    )
    is_box_visible: BoolProperty(
        name="Show cage", default=True, update=_update_box_visibility
    )
    box_size: FloatVectorProperty(
        name="Cage size",
        subtype="XYZ",
        size=3,
        default=(8.0, 4.0, 5.0),
        min=0.1,
    )
    move_step: FloatProperty(name="Move step", default=0.1, min=0.001, soft_max=2.0)
    auto_key: BoolProperty(name="Insert keys while moving", default=True)
    outline_thickness: FloatProperty(
        name="Outline", default=1.5, min=0.1, soft_max=10.0
    )
    screen_width: FloatProperty(name="Width", default=12.0, min=0.1)
    screen_height: FloatProperty(name="Height", default=7.0, min=0.1)
    screen_curvature: FloatProperty(name="Curvature", default=2.0, min=0.0)
    screen_distance: FloatProperty(name="Distance behind", default=2.0, min=0.1)
    screen_segments: IntProperty(name="Segments", default=32, min=2, max=256)
    screen_emission: FloatProperty(name="Emission", default=1.0, min=0.0)
    screen_light_energy: FloatProperty(name="Light power", default=500.0, min=0.0)
    screen_light_color: FloatVectorProperty(
        name="Light color",
        subtype="COLOR",
        size=3,
        default=(1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
    )


classes = (PhantomSettings,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.phantom_settings = PointerProperty(type=PhantomSettings)
    bpy.types.Object.phantom_box_position = FloatVectorProperty(
        name="Cage floor position",
        subtype="TRANSLATION",
        size=3,
    )


def unregister():
    del bpy.types.Object.phantom_box_position
    del bpy.types.Scene.phantom_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
