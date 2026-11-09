"""Named Unicorn CPU choices; these do not identify the fitted camera CPU."""
from unicorn.arm_const import (UC_CPU_ARM_926, UC_CPU_ARM_946, UC_CPU_ARM_1026,
                               UC_CPU_ARM_1136, UC_CPU_ARM_1176, UC_CPU_ARM_TI925T)

CPU_MODELS = {'default': None, 'arm926': UC_CPU_ARM_926, 'arm946': UC_CPU_ARM_946,
              'arm1026': UC_CPU_ARM_1026, 'arm1136': UC_CPU_ARM_1136,
              'arm1176': UC_CPU_ARM_1176, 'ti925t': UC_CPU_ARM_TI925T}


def validate_cpu_model(name):
    if name is None:
        return None
    if type(name) is not str or name not in CPU_MODELS:
        raise ValueError('cpu_model must be one of ' + ', '.join(CPU_MODELS))
    return name


def apply_cpu_model(uc, name=None):
    name = validate_cpu_model(name)
    model = CPU_MODELS[name] if name is not None else None
    if model is not None:
        uc.ctl_set_cpu_model(model)
    actual = uc.ctl_get_cpu_model()
    if model is not None and actual != model:
        raise RuntimeError('Unicorn did not select the requested CPU model')
    aliases = [label for label, value in CPU_MODELS.items() if value == actual]
    return {'requested': name, 'selection_mode': 'implicit-default' if model is None else 'explicit',
            'selected_model_id': actual, 'selected_model_names': aliases,
            'execution_mode': 'ARM big-endian with native ARM/Thumb interworking',
            'physical_cpu_identified': False,
            'limits': ['Unicorn model selection, not identification of the fitted camera CPU',
                       'no physical cache behavior, FIQ delivery or race freedom established']}
