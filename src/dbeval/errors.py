"""Framework-specific exceptions.

The distinction between configuration errors, tool errors, environment errors,
and product failures is intentionally explicit. It is carried through into
results and reports instead of being collapsed into a single failure count.
"""


class DBEvalError(Exception):
    """Base class for expected framework errors."""


class ConfigurationError(DBEvalError):
    """Input configuration is malformed or unsafe."""


class GoldValidationError(DBEvalError):
    """A Gold bundle failed provenance, schema, or freeze validation."""


class EnvironmentError(DBEvalError):
    """The evaluation environment could not provide a valid execution."""


class ToolError(DBEvalError):
    """A declared external tool failed to execute correctly."""


class AdapterError(DBEvalError):
    """A database adapter could not perform a supported operation."""
