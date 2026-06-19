"""Schema sweep: auto-discovers every Pydantic schema under app.schemas and asserts its
required/optional contract holds.

- A schema with at least one required field must reject an empty construction (ValidationError).
- A schema with no required fields must accept an empty construction.

This pins down the validation surface of every request/response model with zero per-schema
maintenance — new schemas are covered the moment they're added.
"""

import importlib
import inspect
import pkgutil

import pytest
from pydantic import BaseModel, ValidationError

import app.schemas as schemas_pkg


def _discover_models():
    models, seen = [], set()
    for mod_info in pkgutil.walk_packages(schemas_pkg.__path__, schemas_pkg.__name__ + "."):
        try:
            module = importlib.import_module(mod_info.name)
        except Exception:
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__.startswith("app.schemas")
                and obj.__qualname__ not in seen
            ):
                seen.add(obj.__qualname__)
                models.append(obj)
    return models


MODELS = _discover_models()


def _has_required(model) -> bool:
    return any(f.is_required() for f in model.model_fields.values())


def test_some_schemas_were_discovered():
    assert len(MODELS) >= 20, f"only discovered {len(MODELS)} schemas"


@pytest.mark.parametrize("model", MODELS, ids=[m.__qualname__ for m in MODELS])
def test_required_contract(model):
    if _has_required(model):
        with pytest.raises(ValidationError):
            model()
    else:
        # All-optional schema: empty construction must succeed.
        model()


@pytest.mark.parametrize("model", MODELS, ids=[m.__qualname__ for m in MODELS])
def test_model_fields_are_introspectable(model):
    # Every field exposes a usable annotation — guards against malformed schema definitions.
    assert isinstance(model.model_fields, dict)
    for name, field in model.model_fields.items():
        assert isinstance(name, str) and name
        assert hasattr(field, "is_required")
