# Phantom Storyboard

A small Blender 5.2+ add-on for staging 3D characters as bounded **Phantoms** and baking their camera view into held 2D animation.

## What works in v0.1

- Wrap a selected model/rig hierarchy in a marked Phantom root, or attach it to an existing Phantom later.
- Align a new cage floor with the active camera's lower frame edge at the character depth, and prevent movement below it while leaving the side walls and ceiling open.
- Move only its attached 3D model with `W/A/S/D`, `Q/E`, rotate with the arrow keys, and insert timeline keys automatically or with `K`. Storyboard Grease Pencil strokes are excluded and detached from older Phantoms automatically.
- Create multiple independent Phantoms.
- Create a curved, UV-mapped backdrop and load an image, GIF, or Blender-supported video.
- Illuminate the scene with an Area Light attached to the backdrop. The backdrop material itself is emissive, so Cycles can also use it as an emitting surface.
- Lock selected objects to background (`Z=10`), Phantom (`Z=0`), or foreground (`Z=-10`) depth.
- Bake the active Phantom through the scene camera to transparent PNGs with Freestyle outlines. Timing can be on ones, twos, threes, or fours.
- Place the baked sequence on one camera-facing plane and switch render output between the editable 3D source and baked 2D result.

## Install

The add-on package is `phantom_storyboard/`. Install that directory as a Blender extension package, or create a ZIP whose archive root contains `__init__.py` and `blender_manifest.toml`, then use **Edit → Preferences → Add-ons → Install from Disk**.

Enable **Phantom Storyboard**. Its panel appears in **3D Viewport → Sidebar → Phantom**.

## Basic workflow

1. Save the `.blend` file. Bake output is written beside it under `phantom_bakes/`.
2. Select the top-level character rig/model and click **Create Phantom**.
   - If the Phantom already exists, select it and click **Load 3D Model** to import FBX, OBJ, glTF/GLB, STL, or PLY directly from disk.
      - For a model already in the scene, select it and click **Attach Selected to Phantom**. With several Phantoms, Shift-select the target Phantom last so it is active.
3. New cages align to the active camera's lower frame edge. Adjust **Cage size** and the selected Phantom's **Cage floor position**, then click **Apply Size / Position**. The floor blocks downward movement; the side walls are visual guides and do not block movement.
4. Use `W/A/S/D` for X/Y, `Q/E` for Z, arrows for Z rotation, and `Esc` to leave control mode.
5. Set the scene camera and frame range.
6. Pick **On ones** (24 poses at 24 fps) or **On twos** (12 poses at 24 fps), then click **Bake to 2D Projection**.
7. Use **Use 2D** / **Use 3D** to choose what final renders see.

For a backdrop, select the target Phantom, set **Distance behind**, and create the screen. With an active camera it is placed behind the Phantom, faces the camera, and curves toward it. Keep the new screen selected and click **Load Image / Video**.

## Bake behavior

The bake uses the current render engine, camera, resolution, lights, and character materials. Other renderable objects are temporarily hidden, Film Transparency and Freestyle are temporarily enabled, and all modified scene settings are restored afterward. Source objects are never deleted. Existing PNGs for the same Phantom are overwritten.

The generated plane contains a full-camera transparent image, positioned at the Phantom root's camera depth. This keeps the projection aligned with the shot while allowing several independently baked Phantoms.

## Current limits

- The bake is a raster PNG sequence, not editable Grease Pencil vectors. This avoids fragile tracing APIs and external OpenCV dependencies in the first release.
- Bundled walk/jump actions and key-to-action mapping are not included yet; existing Blender Actions can still animate the rig before baking.
- The attached Area Light has one color; it does not reproduce every pixel of a video as spatial Eevee lighting. Cycles emission gives physically based image lighting at a higher render cost.
- Animated GIF support depends on the media codecs in the Blender build. Convert unsupported GIFs to MP4.
- The Z-order buttons implement the requested fixed world-Z convention. In camera setups that use another depth axis, use normal Blender transforms instead.

## Development

Run the Blender-independent checks with:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q phantom_storyboard tests
```

Run the Blender registration, scene, and real render smoke test with:

```sh
blender --background --factory-startup --python tests/blender_smoke.py
```

Build an installable extension archive with:

```sh
blender --command extension build --source-dir phantom_storyboard --output-dir .
```
