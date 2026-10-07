# Seed model dictionaries

One file per brand, named by the brand slug. Seeds are imported as confirmed groups when a
brand is grouped; existing groups (including ones the user merged or marked "not a model")
are never overwritten or recreated.

```yaml
models:
  - name: Geobasket            # display name, also an alias
    aliases: [geo basket]      # extra spellings; normalized like titles
    types: [hitop_sneakers]    # taxonomy types, or "any" for every type of the brand
    infer: hitop_sneakers      # optional: type to pick when a category is ambiguous
    parent: Track              # optional: the line this model is a version of
```

An optional `generic` list holds the marks of the brand (`paris`, `maison`, `demna`…): phrases
that are never a model of this brand on their own, only a description of the `brandmark`
class. A seed of the same name still wins for its own types (`Paris` for sneakers).

```yaml
generic:
  - paris
  - bb signature
```

These seeds were written from known product names, not from mined live data. After the
first live collection, review `python -m app.cli grouping-report --brand <name>` and the
"auto" groups in the UI, then add confirmed names here.
