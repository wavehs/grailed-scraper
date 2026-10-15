# Seed model dictionaries

One file per brand, named by the brand slug. Seeds are imported as confirmed groups when a
brand is grouped; existing groups (including ones the user merged or marked "not a model")
are never overwritten or recreated. Aliases added to a seed later are appended to its existing
group (never to a tombstone); aliases are never removed. One letter and a hyphen make one word,
so `X-Pander` here and `Xpander` in a title are the same name.

```yaml
models:
  - name: Geobasket            # display name, also an alias
    aliases: [geo basket]      # extra spellings; normalized like titles
    types: [hitop_sneakers]    # taxonomy types, or "any": every type whose titles name it
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

An optional `collabs` list is the brand's whitelist of collaborations. A listing that matches
no model but whose title names the partner (`name`) or one of its `aliases` goes to the line
`_collab-<partner>` of its product type ("Yeezy Gap" -> `_collab-yeezy-gap`), before
descriptions and "No model". Names and aliases are matched like titles (folded, respelled)
and are also brand terms, so their words never stay in a title as model words ("engineered"
from "Engineered by Balenciaga"). `designers` are Grailed designer names (default: the name);
they never place a listing and only feed `grouping-report --collabs`. `only_sections` limits a
partner to taxonomy sections (Crocs: `footwear`), and `not_before` lists words that cancel a
match right after the phrase ("croc embossed" is leather, not Crocs). A phrase may belong to
one collaboration only. The file is part of the policy digest, so editing the list regroups
the brand in full.

```yaml
collabs:
  - name: Yeezy Gap
    aliases: [yeezy gap, ygebb, yzy gap, engineered by balenciaga]
    designers: [Gap, Yeezy, Yeezy Gap, Kanye West]
  - name: Crocs
    designers: [Crocs]
    only_sections: [footwear]
    not_before: [embossed, effect, leather]
```

A seed of `types: any` gets a group in a product type only once a title of that type names
it (the same matching as assignment: longest phrase, glued spelling, typo). Its group in a type
no title names any longer retires when empty and comes back with its id.

These seeds were written from known product names, not from mined live data. After the
first live collection, review `python -m app.cli grouping-report --brand <name> --candidates`
(top mined lines with example titles and possible merges) and the "auto" groups in the UI,
then add confirmed names here: the seed takes the mined group with its id.

`golden-goose`, `enfants-riches-deprimes` and `maison-margiela` were extended this way from
the live candidates; `backend/tests/test_brand_dictionaries.py` pins their seeds to real titles
and checks that no alias is eaten by the title normalizer (a dropped alias silently matches
nothing).
