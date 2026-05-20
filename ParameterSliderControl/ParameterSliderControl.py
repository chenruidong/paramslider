import math
import os
import traceback

import adsk.core
import adsk.fusion


handlers = []

CMD_ID = 'ParameterSliderControl_Command'
CMD_NAME = 'Parameter Sliders'
CMD_DESC = 'Control named user parameters with slider controls.'
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidModifyPanel'

ANGLE_TARGET_PARAMS = ['chamber_a1', 'chamber_a2', 'chamber_a3']
OFFSET_TARGET_PARAMS = ['chamber_x', 'chamber_y', 'chamber_z']
TARGET_PARAMS = ANGLE_TARGET_PARAMS + OFFSET_TARGET_PARAMS

ANGLE_UNITS = {'deg', 'degree', 'degrees', 'rad', 'radian', 'radians'}
DISTANCE_UNITS = {'mm', 'millimeter', 'millimeters', 'cm', 'centimeter',
                  'centimeters', 'm', 'meter', 'meters', 'in', 'inch',
                  'inches', 'ft', 'foot', 'feet'}

INPUT_PREFIX_SLIDER = 'psc_slider_'
INPUT_PREFIX_SPINNER = 'psc_spinner_'
INPUT_PREFIX_TEXT = 'psc_text_'
INPUT_PREFIX_MIN = 'psc_min_'
INPUT_PREFIX_MAX = 'psc_max_'
FORCE_COMPUTE_INPUT_ID = 'psc_force_compute'


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if not cmd_def:
            cmd_def = ui.commandDefinitions.addButtonDefinition(
                CMD_ID,
                CMD_NAME,
                CMD_DESC,
                _resource_folder()
            )

        on_created = CommandCreatedHandler()
        cmd_def.commandCreated.add(on_created)
        handlers.append(on_created)

        panel = _toolbar_panel(ui)
        if panel:
            control = panel.controls.itemById(CMD_ID)
            if not control:
                control = panel.controls.addCommand(cmd_def)
                control.isPromoted = True
        else:
            _log('Parameter Slider Control: could not find a toolbar panel.')

    except Exception:
        if ui:
            ui.messageBox('Parameter Slider Control failed to start:\n{}'.format(traceback.format_exc()))


def stop(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        panel = _toolbar_panel(ui)
        if panel:
            control = panel.controls.itemById(CMD_ID)
            if control:
                control.deleteMe()

        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()

        handlers.clear()

    except Exception:
        if ui:
            ui.messageBox('Parameter Slider Control failed to stop:\n{}'.format(traceback.format_exc()))


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        try:
            cmd = adsk.core.Command.cast(args.command)
            cmd.isAutoExecute = False
            inputs = cmd.commandInputs

            app = adsk.core.Application.get()
            design = adsk.fusion.Design.cast(app.activeProduct)
            if not design:
                inputs.addTextBoxCommandInput(
                    'psc_no_design',
                    'Error',
                    'No active Fusion design found.',
                    2,
                    True
                )
                return

            specs = _target_parameter_specs(design)
            if not specs:
                inputs.addTextBoxCommandInput(
                    'psc_no_params',
                    'No target parameters',
                    'Could not find compatible user parameters named chamber_a1, chamber_a2, chamber_a3, chamber_x, chamber_y, or chamber_z.',
                    2,
                    True
                )
                skipped = _incompatible_target_parameters(design)
                if skipped:
                    inputs.addTextBoxCommandInput(
                        'psc_skipped_params',
                        'Skipped parameters',
                        'Unsupported units: {}'.format(', '.join(skipped)),
                        2,
                        True
                    )
                available = _compatible_parameter_names(design)
                if available:
                    inputs.addTextBoxCommandInput(
                        'psc_available_params',
                        'Compatible parameters',
                        ', '.join(available),
                        3,
                        True
                    )
                return

            inputs.addBoolValueInput(
                FORCE_COMPUTE_INPUT_ID,
                'Force Compute All after each change',
                True,
                '',
                False
            )

            table = inputs.addTableCommandInput(
                'psc_slider_table',
                'Parameter controls',
                5,
                '1.4:0.8:3:0.8:0.9'
            )
            _configure_table(table, len(specs))

            input_map = {}
            range_map = {}
            for row, spec in enumerate(specs):
                _add_parameter_controls(inputs, table, row, spec)
                input_map[_slider_id(spec.name)] = spec.name
                input_map[_spinner_id(spec.name)] = spec.name
                range_map[_min_id(spec.name)] = spec.name
                range_map[_max_id(spec.name)] = spec.name

            skipped = _incompatible_target_parameters(design)
            if skipped:
                inputs.addTextBoxCommandInput(
                    'psc_skipped_params',
                    'Skipped parameters',
                    'Unsupported units: {}'.format(', '.join(skipped)),
                    2,
                    True
                )

            state = SliderCommandState()

            on_changed = InputChangedHandler(input_map, range_map, state)
            cmd.inputChanged.add(on_changed)
            handlers.append(on_changed)

            on_preview = CommandExecutePreviewHandler(input_map, state)
            cmd.executePreview.add(on_preview)
            handlers.append(on_preview)

            on_execute = CommandExecuteHandler(input_map, state)
            cmd.execute.add(on_execute)
            handlers.append(on_execute)

            on_destroy = CommandDestroyHandler(state)
            cmd.destroy.add(on_destroy)
            handlers.append(on_destroy)

        except Exception:
            _show_error('Parameter Slider Control command failed', traceback.format_exc())


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def __init__(self, input_map, range_map, state):
        super().__init__()
        self.input_map = input_map
        self.range_map = range_map
        self.state = state
        self.is_updating = False

    def notify(self, args):
        if self.is_updating:
            return

        try:
            changed = adsk.core.CommandInput.cast(args.input)
            if not changed:
                return

            if changed.id in self.range_map:
                self._handle_range_change(changed)
                return

            if changed.id not in self.input_map:
                return

            app = adsk.core.Application.get()
            design = adsk.fusion.Design.cast(app.activeProduct)
            if not design:
                _show_error('Parameter Slider Control', 'No active Fusion design found.')
                return

            param_name = self.input_map[changed.id]
            param = design.userParameters.itemByName(param_name)
            if not param:
                _show_error('Parameter Slider Control', 'User parameter "{}" was not found.'.format(param_name))
                return

            spec = _parameter_spec(design, param)
            if not spec:
                _show_error(
                    'Parameter Slider Control',
                    'User parameter "{}" has incompatible units "{}".'.format(param_name, _param_unit(param))
                )
                return

            value = _changed_value(changed, spec)
            if value is None:
                return

            value = _clamp(_snap(value, spec.step), spec.minimum, spec.maximum)
            expression = _expression_for_value(design, spec, value)

            self.is_updating = True
            try:
                inputs = changed.commandInputs
                _set_matching_inputs(inputs, param_name, value, spec, changed.id)
                _set_expression_text(inputs, param_name, expression)
            finally:
                self.is_updating = False

        except Exception:
            self.is_updating = False
            _log('Parameter Slider Control update failed: {}'.format(traceback.format_exc()))

    def _handle_range_change(self, changed):
        inputs = changed.commandInputs
        param_name = self.range_map[changed.id]
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            _show_error('Parameter Slider Control', 'No active Fusion design found.')
            return

        param = design.userParameters.itemByName(param_name)
        if not param:
            _show_error('Parameter Slider Control', 'User parameter "{}" was not found.'.format(param_name))
            return

        spec = _parameter_spec(design, param)
        if not spec:
            _show_error(
                'Parameter Slider Control',
                'User parameter "{}" has incompatible units "{}".'.format(param_name, _param_unit(param))
            )
            return
        _apply_saved_range(spec, self.state.ranges.get(param_name))

        minimum = _range_value_from_input(inputs, _min_id(param_name), spec)
        maximum = _range_value_from_input(inputs, _max_id(param_name), spec)
        if minimum is None or maximum is None:
            return

        if maximum <= minimum:
            if changed.id == _min_id(param_name):
                minimum = maximum - spec.step
            else:
                maximum = minimum + spec.step

        current = _value_from_inputs(inputs, param_name, spec)
        if current is None:
            current = spec.current_value
        current = _clamp(current, minimum, maximum)
        expression = _expression_for_value(design, spec, current)

        self.is_updating = True
        try:
            _set_range_inputs(inputs, param_name, minimum, maximum, spec)
            _set_slider_range(inputs, param_name, minimum, maximum, current, spec)
            _set_matching_inputs(inputs, param_name, current, spec, None)
            _set_expression_text(inputs, param_name, expression)
            self.state.ranges[param_name] = (minimum, maximum)
        finally:
            self.is_updating = False

class CommandExecutePreviewHandler(adsk.core.CommandEventHandler):
    def __init__(self, input_map, state):
        super().__init__()
        self.input_map = input_map
        self.state = state

    def notify(self, args):
        try:
            event_args = adsk.core.CommandEventArgs.cast(args)
            command = event_args.command
            _apply_input_values_to_parameters(
                command.commandInputs,
                self.input_map,
                self.state,
                None,
                _force_compute_enabled(command.commandInputs)
            )
            event_args.isValidResult = True
        except Exception:
            _show_error('Parameter Slider Control preview failed', traceback.format_exc())


class CommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, input_map, state):
        super().__init__()
        self.input_map = input_map
        self.state = state

    def notify(self, args):
        try:
            event_args = adsk.core.CommandEventArgs.cast(args)
            command = event_args.command
            _apply_input_values_to_parameters(
                command.commandInputs,
                self.input_map,
                self.state,
                None,
                True
            )
        except Exception:
            _show_error('Parameter Slider Control execute failed', traceback.format_exc())


class CommandDestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def notify(self, args):
        self.state.last_expressions.clear()
        self.state.ranges.clear()


class SliderCommandState:
    def __init__(self):
        self.last_expressions = {}
        self.ranges = {}


class ParameterSpec:
    def __init__(self, name, kind, display_unit, minimum, maximum, step, current_value, expression,
                 hard_minimum=None, hard_maximum=None, input_step=None):
        self.name = name
        self.kind = kind
        self.display_unit = display_unit
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.input_step = input_step if input_step is not None else step
        self.current_value = current_value
        self.expression = expression
        self.hard_minimum = hard_minimum if hard_minimum is not None else minimum
        self.hard_maximum = hard_maximum if hard_maximum is not None else maximum


def _add_parameter_controls(inputs, table, row, spec):
    current_value = _clamp(spec.current_value, spec.minimum, spec.maximum)
    spec.range_minimum = spec.minimum
    spec.range_maximum = spec.maximum

    text = inputs.addTextBoxCommandInput(
        _text_id(spec.name),
        spec.name,
        '{}\n{}'.format(spec.name, spec.expression),
        2,
        True
    )
    table.addCommandInput(text, row, 0)

    minimum = inputs.addFloatSpinnerCommandInput(
        _min_id(spec.name),
        'Min',
        spec.display_unit,
        spec.hard_minimum,
        spec.hard_maximum,
        spec.input_step,
        spec.minimum
    )
    _set_spinner_value(minimum, spec.minimum, spec)
    minimum.tooltip = 'Minimum slider value for {}'.format(spec.name)
    table.addCommandInput(minimum, row, 1)

    slider = inputs.addFloatSliderCommandInput(
        _slider_id(spec.name),
        spec.name,
        spec.display_unit,
        spec.minimum,
        spec.maximum,
        False
    )
    slider.valueOne = current_value
    table.addCommandInput(slider, row, 2)

    maximum = inputs.addFloatSpinnerCommandInput(
        _max_id(spec.name),
        'Max',
        spec.display_unit,
        spec.hard_minimum,
        spec.hard_maximum,
        spec.input_step,
        spec.maximum
    )
    _set_spinner_value(maximum, spec.maximum, spec)
    maximum.tooltip = 'Maximum slider value for {}'.format(spec.name)
    table.addCommandInput(maximum, row, 3)

    spinner = inputs.addFloatSpinnerCommandInput(
        _spinner_id(spec.name),
        'Value',
        spec.display_unit,
        spec.hard_minimum,
        spec.hard_maximum,
        spec.input_step,
        current_value
    )
    _set_spinner_value(spinner, current_value, spec)
    spinner.tooltip = 'Direct numeric control for {}'.format(spec.name)
    table.addCommandInput(spinner, row, 4)


def _configure_table(table, row_count):
    try:
        table.minimumVisibleRows = max(2, min(row_count, 6))
    except Exception:
        _log('Parameter Slider Control: could not set table minimum visible rows.')

    try:
        table.maximumVisibleRows = max(2, min(max(row_count, 2), 8))
    except Exception:
        _log('Parameter Slider Control: could not set table maximum visible rows.')

    try:
        table.hasGrid = False
    except Exception:
        pass


def _apply_input_values_to_parameters(inputs, input_map, state, param_names=None, force_compute=False):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        _show_error('Parameter Slider Control', 'No active Fusion design found.')
        return

    updated = False
    names = param_names if param_names else sorted(set(input_map.values()))
    for param_name in names:
        param = design.userParameters.itemByName(param_name)
        if not param:
            _show_error('Parameter Slider Control', 'User parameter "{}" was not found.'.format(param_name))
            continue

        spec = _parameter_spec(design, param)
        if not spec:
            _show_error(
                'Parameter Slider Control',
                'User parameter "{}" has incompatible units "{}".'.format(param_name, _param_unit(param))
            )
            continue

        _apply_saved_range(spec, state.ranges.get(param_name))

        value = _value_from_inputs(inputs, param_name, spec)
        if value is None:
            continue

        value = _clamp(_snap(value, spec.step), spec.minimum, spec.maximum)
        expression = _expression_for_value(design, spec, value)
        if state.last_expressions.get(param_name) == expression and _param_expression(param) == expression:
            continue

        try:
            param.expression = expression
            _set_expression_text(inputs, param_name, param.expression)
            state.last_expressions[param_name] = param.expression
            updated = True
        except Exception:
            _show_error(
                'Parameter Slider Control',
                'Could not update "{}" to "{}".'.format(param_name, expression)
            )

    if updated:
        if force_compute:
            design.computeAll()
        _refresh_viewport()


def _target_parameter_specs(design):
    specs = []
    for name in TARGET_PARAMS:
        param = design.userParameters.itemByName(name)
        if not param:
            continue

        spec = _parameter_spec(design, param)
        if spec:
            specs.append(spec)
        else:
            _log('Parameter Slider Control: skipped "{}" because unit "{}" is not supported.'.format(name, _param_unit(param)))

    return specs


def _compatible_parameter_names(design):
    names = []
    user_params = design.userParameters
    for index in range(user_params.count):
        param = user_params.item(index)
        if _parameter_spec(design, param):
            names.append(param.name)
    return names


def _incompatible_target_parameters(design):
    skipped = []
    for name in TARGET_PARAMS:
        param = design.userParameters.itemByName(name)
        if param and not _parameter_spec(design, param):
            skipped.append('{} ({})'.format(name, _param_unit(param) or 'no units'))
    return skipped


def _apply_saved_range(spec, saved_range):
    if not saved_range:
        return

    minimum, maximum = saved_range
    if maximum <= minimum:
        return

    spec.minimum = minimum
    spec.maximum = maximum


def _parameter_spec(design, param):
    unit = _normalized_unit(_param_unit(param))
    expression = _param_expression(param)

    if unit in ANGLE_UNITS or _looks_like_angle(expression):
        return ParameterSpec(
            param.name,
            'angle',
            'deg',
            -math.pi,
            math.pi,
            math.radians(0.1),
            _angle_value(param),
            expression,
            -math.pi,
            math.pi,
            0.1
        )

    if unit in DISTANCE_UNITS:
        display_unit = 'mm' if param.name in OFFSET_TARGET_PARAMS else _document_distance_unit(design)
        default_minimum = -10.0 if param.name in OFFSET_TARGET_PARAMS else -100.0
        default_maximum = 10.0 if param.name in OFFSET_TARGET_PARAMS else 100.0
        step = 0.01 if param.name in OFFSET_TARGET_PARAMS else 1.0
        return ParameterSpec(
            param.name,
            'distance',
            display_unit,
            _to_internal_distance(design, default_minimum, display_unit),
            _to_internal_distance(design, default_maximum, display_unit),
            abs(_to_internal_distance(design, step, display_unit)),
            _distance_value(param),
            expression,
            _to_internal_distance(design, -10000.0, display_unit),
            _to_internal_distance(design, 10000.0, display_unit),
            step
        )

    if unit == '' or unit == 'unitless':
        default_minimum = -10.0 if param.name in OFFSET_TARGET_PARAMS else 0.0
        default_maximum = 10.0 if param.name in OFFSET_TARGET_PARAMS else 1.0
        return ParameterSpec(
            param.name,
            'unitless',
            '',
            default_minimum,
            default_maximum,
            0.01,
            float(param.value),
            expression,
            -10000.0,
            10000.0
        )

    return None


def _angle_value(param):
    try:
        return float(param.value)
    except Exception:
        _log('Parameter Slider Control: could not parse angle value for "{}".'.format(param.name))
        return 0.0


def _distance_value(param):
    try:
        return float(param.value)
    except Exception:
        _log('Parameter Slider Control: could not parse distance value for "{}".'.format(param.name))
        return 0.0


def _to_internal_distance(design, value, display_unit):
    units_mgr = design.unitsManager
    try:
        return float(units_mgr.convert(value, display_unit, 'cm'))
    except Exception:
        return value


def _document_distance_unit(design):
    try:
        default_units = _normalized_unit(design.unitsManager.defaultLengthUnits)
        if default_units:
            if default_units in ('"', 'inch', 'inches'):
                return 'in'
            if default_units in ("'", 'foot', 'feet'):
                return 'ft'
            return default_units
    except Exception:
        pass
    return 'mm'


def _changed_value(command_input, spec):
    slider = adsk.core.FloatSliderCommandInput.cast(command_input)
    if slider:
        return float(slider.valueOne)

    spinner = adsk.core.FloatSpinnerCommandInput.cast(command_input)
    if spinner:
        return float(spinner.value)

    return None


def _value_from_inputs(inputs, param_name, spec):
    slider = adsk.core.FloatSliderCommandInput.cast(inputs.itemById(_slider_id(param_name)))
    if slider:
        return float(slider.valueOne)

    spinner = adsk.core.FloatSpinnerCommandInput.cast(inputs.itemById(_spinner_id(param_name)))
    if spinner:
        return float(spinner.value)

    return None


def _range_value_from_input(inputs, input_id, spec):
    spinner = adsk.core.FloatSpinnerCommandInput.cast(inputs.itemById(input_id))
    if spinner:
        return float(spinner.value)
    return None


def _force_compute_enabled(inputs):
    force_input = adsk.core.BoolValueCommandInput.cast(inputs.itemById(FORCE_COMPUTE_INPUT_ID))
    return bool(force_input and force_input.value)


def _refresh_viewport():
    try:
        viewport = adsk.core.Application.get().activeViewport
        if viewport:
            viewport.refresh()
    except Exception:
        pass


def _set_matching_inputs(inputs, param_name, value, spec, changed_id):
    slider = adsk.core.FloatSliderCommandInput.cast(inputs.itemById(_slider_id(param_name)))
    if slider and slider.id != changed_id:
        slider.valueOne = value

    spinner = adsk.core.FloatSpinnerCommandInput.cast(inputs.itemById(_spinner_id(param_name)))
    if spinner and spinner.id != changed_id:
        _set_spinner_value(spinner, value, spec)

    if not changed_id:
        return

    changed = inputs.itemById(changed_id)
    changed_slider = adsk.core.FloatSliderCommandInput.cast(changed)
    if changed_slider:
        changed_slider.valueOne = value

    changed_spinner = adsk.core.FloatSpinnerCommandInput.cast(changed)
    if changed_spinner:
        _set_spinner_value(changed_spinner, value, spec)


def _set_range_inputs(inputs, param_name, minimum, maximum, spec):
    minimum_input = adsk.core.FloatSpinnerCommandInput.cast(inputs.itemById(_min_id(param_name)))
    if minimum_input:
        _set_spinner_value(minimum_input, minimum, spec)

    maximum_input = adsk.core.FloatSpinnerCommandInput.cast(inputs.itemById(_max_id(param_name)))
    if maximum_input:
        _set_spinner_value(maximum_input, maximum, spec)


def _set_slider_range(inputs, param_name, minimum, maximum, current_value, spec):
    slider = adsk.core.FloatSliderCommandInput.cast(inputs.itemById(_slider_id(param_name)))
    if slider:
        slider.minimumValue = min(slider.minimumValue, minimum, current_value)
        slider.maximumValue = max(slider.maximumValue, maximum, current_value)
        slider.valueOne = current_value
        slider.minimumValue = minimum
        slider.maximumValue = maximum

    spinner = adsk.core.FloatSpinnerCommandInput.cast(inputs.itemById(_spinner_id(param_name)))
    if spinner:
        _set_spinner_value(spinner, current_value, spec)


def _set_spinner_value(spinner, value, spec):
    if spec.kind == 'angle':
        spinner.expression = _format_degrees(value)
        return

    spinner.value = value


def _set_expression_text(inputs, param_name, text):
    text_input = adsk.core.TextBoxCommandInput.cast(inputs.itemById(_text_id(param_name)))
    if text_input:
        text_input.text = '{}\n{}'.format(param_name, text)


def _expression_for_value(design, spec, value):
    if spec.kind == 'unitless':
        return '{:.3f}'.format(value)

    if spec.kind == 'angle':
        return '{} deg'.format(_format_degrees(value))

    display_value = design.unitsManager.convert(value, 'cm', spec.display_unit)
    return '{:.3f} {}'.format(display_value, spec.display_unit)


def _format_degrees(value):
    return '{:.3f}'.format(math.degrees(value))


def _snap(value, step):
    if step <= 0:
        return value
    return round(value / step) * step


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _looks_like_angle(expression):
    lower = expression.lower()
    return 'deg' in lower or 'rad' in lower


def _param_unit(param):
    try:
        return param.unit or ''
    except Exception:
        return ''


def _param_expression(param):
    try:
        return param.expression or ''
    except Exception:
        return ''


def _normalized_unit(unit):
    return (unit or '').strip().lower()


def _toolbar_panel(ui):
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if not workspace:
        return None

    panel = workspace.toolbarPanels.itemById(PANEL_ID)
    if panel:
        return panel

    for fallback_id in ('SolidScriptsAddinsPanel', 'SolidCreatePanel'):
        panel = workspace.toolbarPanels.itemById(fallback_id)
        if panel:
            return panel

    return None


def _resource_folder():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')


def _slider_id(name):
    return INPUT_PREFIX_SLIDER + name


def _spinner_id(name):
    return INPUT_PREFIX_SPINNER + name


def _min_id(name):
    return INPUT_PREFIX_MIN + name


def _max_id(name):
    return INPUT_PREFIX_MAX + name


def _text_id(name):
    return INPUT_PREFIX_TEXT + name


def _show_error(title, message):
    try:
        adsk.core.Application.get().userInterface.messageBox('{}:\n{}'.format(title, message))
    except Exception:
        _log('{}: {}'.format(title, message))


def _log(message):
    try:
        adsk.core.Application.get().log(message)
    except Exception:
        pass
