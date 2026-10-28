# Phantom Storyboard

A small Blender 4.2+ add-on for staging 3D characters as bounded **Phantoms** and baking their camera view into held 2D animation.

## What works in v0.1

- Wrap a selected model/rig hierarchy in a marked Phantom root.
- Keep that root inside a visible wire cage with a `Limit Location` constraint.
- Move it with `W/A/S/D`, `Q/E`, rotate with the arrow keys, and insert timeline keys automatically or with `K`.
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
3. Set **Cage size**, click **Apply Cage Size**, then click **Control Phantom**.
4. Use `W/A/S/D` for X/Y, `Q/E` for Z, arrows for Z rotation, and `Esc` to leave control mode.
5. Set the scene camera and frame range.
6. Pick **On ones** (24 poses at 24 fps) or **On twos** (12 poses at 24 fps), then click **Bake to 2D Projection**.
7. Use **Use 2D** / **Use 3D** to choose what final renders see.

For a backdrop, set its dimensions, create it, keep it selected, and click **Load Image / Video**. The screen faces local `-Y`; rotate it as needed for your shot.

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
