# Parameter Slider Control

Fusion 360 add-in that adds a command dialog with slider controls for selected user parameters.

## Included Add-In

- `ParameterSliderControl/ParameterSliderControl.py`
- `ParameterSliderControl/ParameterSliderControl.manifest`
- `ParameterSliderControl/resources/`

## Target Parameters

The command looks for compatible user parameters with these names:

- `chamber_a1`
- `chamber_a2`
- `chamber_a3`
- `chamber_x`
- `chamber_y`
- `chamber_z`

Angle parameters support degree/radian units. Offset parameters support common distance units.

## Install

Copy the `ParameterSliderControl` folder into your Fusion 360 add-ins folder, then enable it from Fusion 360's Scripts and Add-Ins dialog.

