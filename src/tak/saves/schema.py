# @author Daniel McCoy Stephenson
import json

from jsonschema import validate


def validateAgainstSchema(data, schemaPath):
    """Validate data against the JSON Schema at schemaPath.

    Raises jsonschema.exceptions.ValidationError if data doesn't conform
    (e.g. a value outside the range the schema declares as valid). A game
    validates on every load *and* every save: a save that fails on the way
    out is a bug caught now, not a damaged file found next session.
    A schema file that is not valid JSON raises json.JSONDecodeError naming
    schemaPath, so the author knows which of a game's schemas to fix.
    """
    with open(schemaPath) as schemaFile:
        try:
            schema = json.load(schemaFile)
        except json.JSONDecodeError as error:
            # Same type, so a caller already catching it is unaffected; the
            # bare message gives only a line and column, never the file.
            raise json.JSONDecodeError(
                "schema %s is not valid JSON: %s" % (schemaPath, error.msg),
                error.doc,
                error.pos,
            ) from None
    validate(instance=data, schema=schema)
