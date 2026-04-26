from pipe.m.optionvar import BoolOptionVar, IntOptionVar, StringOptionVar


class RigBuilderSettings:
    DEV_BUILD = BoolOptionVar("rigBuilder.devBuild", False)
    LAST_TAB = IntOptionVar("rigBuilder.lastTab", 0)
    LAST_CHARACTER_RIG = StringOptionVar("rigBuilder.lastCharacterRig", "")
    LAST_CHARACTER_VARIANT = StringOptionVar("rigBuilder.lastCharacterVariant", "")
    LAST_PROP_RIG = StringOptionVar("rigBuilder.lastPropRig", "")
    LAST_PROP_VARIANT = StringOptionVar("rigBuilder.lastPropVariant", "")
    LAST_CHARACTER_SCOPE = StringOptionVar("rigBuilder.lastCharacterScope", "")
    LAST_PROP_SCOPE = StringOptionVar("rigBuilder.lastPropScope", "")
    LOCAL_OVERRIDE = BoolOptionVar("rigBuilder.localOverride", False)
    LAST_OVERRIDE_DIR = StringOptionVar("rigBuilder.lastOverrideDir", "")
