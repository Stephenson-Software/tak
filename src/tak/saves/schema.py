# @author Daniel McCoy Stephenson
import json

from jsonschema import validate


def validateAgainstSchema(data, schemaPath):
    """Validate data against the JSON Schema at schemaPath.

    Raises jsonschema.exceptions.ValidationError if data doesn't conform
    (e.g. a value outside the range the schema declares as valid). A game
    validates on every load *and* every save: a save that fails on the way
    out is a bug caught now, not a damaged file found next session.
    """
    with open(schemaPath) as schemaFile:
        schema = json.load(schemaFile)
    validate(instance=data, schema=schema)
