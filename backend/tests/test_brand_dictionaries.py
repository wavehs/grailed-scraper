"""Seed dictionaries of the collected brands catch their liquid lines (v2 plan 1.2).

Titles are real ones from the live index; each must reach its seed or collaboration.
"""

from __future__ import annotations

import pytest

from app.db.models import Brand
from app.services.grouping.assign import ModelDictionary, ModelEntry
from app.services.grouping.brands import brand_normalizer
from app.services.grouping.collabs import CollabIndex
from app.services.grouping.normalize import TitleNormalizer
from app.services.grouping.policy import GroupingPolicy, load_policy

BRANDS = {
    "golden-goose": ("Golden Goose", ["Golden Goose", "GGDB"]),
    "maison-margiela": ("Maison Margiela", ["Maison Margiela", "Margiela", "MM6"]),
    "enfants-riches-deprimes": ("Enfants Riches Déprimés", ["Enfants Riches Déprimés", "ERD"]),
}
# Types an "any" seed is tried in: the ones its titles below come from.
ANY_TYPES = ("tshirt", "longsleeve", "hoodie", "bag", "light_jacket")


@pytest.fixture(scope="module")
def policy() -> GroupingPolicy:
    return load_policy()


def _normalizer(policy: GroupingPolicy, slug: str) -> TitleNormalizer:
    name, terms = BRANDS[slug]
    return brand_normalizer(policy, Brand(name=name, slug=slug, aliases=[]), terms)


def _model(policy: GroupingPolicy, slug: str, product_type: str, title: str) -> str | None:
    normalizer = _normalizer(policy, slug)
    seeds = policy.seed_models(slug)
    entries = [
        ModelEntry(
            key=index,
            product_type=product_type,
            name=seed.name,
            phrases=tuple(tokens for value in seed.aliases if (tokens := normalizer.phrase(value))),
        )
        for index, seed in enumerate(seeds)
        if product_type in (seed.types or ANY_TYPES)
    ]
    dictionary = ModelDictionary(
        entries,
        protected_modifiers=policy.words.protected_modifiers,
        version_numbers=policy.words.version_numbers,
    )
    match = dictionary.match(product_type, normalizer.normalize(title).tokens)
    return match.entry.name if match else None


@pytest.mark.parametrize(
    ("slug", "product_type", "title", "expected"),
    [
        ("golden-goose", "lowtop_sneakers", "Mens Golden Goose Super Star Sneakers", "Superstar"),
        ("golden-goose", "shoes", "Golden Goose Superstar", "Superstar"),
        ("golden-goose", "hitop_sneakers", "Golden Goose Ball Star", "Ball Star"),
        (
            "golden-goose",
            "lowtop_sneakers",
            "Golden Goose Deluxe Brand Ballstar leather",
            "Ball Star",
        ),
        ("golden-goose", "lowtop_sneakers", "Golden Goose Dad Star Running Grey", "Dad Star"),
        ("golden-goose", "lowtop_sneakers", "Golden Goose Running Sole Sneakers", "Running Sole"),
        ("golden-goose", "lowtop_sneakers", "Golden Goose Pure Star Distressed", "Purestar"),
        ("golden-goose", "hitop_sneakers", "Golden Goose Mid Star GGDB Distressed", "Mid Star"),
        ("golden-goose", "hitop_sneakers", "Golden Goose Sky star Size 10", "Sky Star"),
        ("golden-goose", "lowtop_sneakers", "GOLDEN GOOSE BLACK CANVAS VSTAR", "V-Star"),
        ("golden-goose", "lowtop_sneakers", "Golden goose star Dan", "Stardan"),
        # "Slide" is a sandals word: the model is named by "Penstar".
        ("golden-goose", "hitop_sneakers", "Golden Goose Slide Penstar Classic", "Penstar"),
        ("golden-goose", "boots", "GOLDEN GOOSE WISH STAR LEATHER COWBOY BOOTS", "Wish Star"),
        ("maison-margiela", "hitop_sneakers", "maison margiela gats burgundy", "GAT"),
        ("maison-margiela", "shoes", "Maison Margiela Replica GAT Sneakers", "GAT"),
        ("maison-margiela", "slip_ons", "Maison Margiela Tabi Babouche", "Tabi"),
        (
            "maison-margiela",
            "lowtop_sneakers",
            "Maison Margiela New Evolution Sneakers",
            "Evolution",
        ),
        ("maison-margiela", "hitop_sneakers", "Margiela DDStcks Navy Hightops", "DDSTCK"),
        ("maison-margiela", "lowtop_sneakers", "MAISON MARGIELA BURGUNDY SPRINTER", "Sprinter"),
        (
            "maison-margiela",
            "wallet",
            "Maison Margiela Four Stitches Grained Wallet",
            "Four Stitches",
        ),
        ("maison-margiela", "sweater", "Maison Margiela 2015 Elbow Patch Crewneck", "Elbow Patch"),
        ("maison-margiela", "tshirt", "Maison Margiela Aids Awareness Tee Archive", "AIDS"),
        ("maison-margiela", "hoodie", "MM6 Maison Margiela Embroidered Number Hoodie", "Numbers"),
        ("maison-margiela", "hitop_sneakers", "Maison Margiela x Reebok Question Mid", "Question"),
        ("enfants-riches-deprimes", "tshirt", "Teenage Snuff Film T-Shirt", "Teenage Snuff Film"),
        ("enfants-riches-deprimes", "tshirt", "ERD Devil In Disguise Shirt", "Devil in Disguise"),
        (
            "enfants-riches-deprimes",
            "hoodie",
            "Enfants Riches Deprimes Benny's Video Hoodie",
            "Benny's Video",
        ),
        (
            "enfants-riches-deprimes",
            "bag",
            "Enfants Riches Deprimes Anti Public Library bag",
            "Anti Public Library",
        ),
        (
            "enfants-riches-deprimes",
            "hoodie",
            "ENFANTS RICHES DEPRIMES My Role in the War Hoodie",
            "My Role in the War",
        ),
        (
            "enfants-riches-deprimes",
            "sunglasses",
            "ERD Isolar 101 MADE IN FRANCE SUNGLASSES",
            "Isolar",
        ),
        (
            "enfants-riches-deprimes",
            "boots",
            "ERD Midnight Cowboy Boots leather and suede",
            "Midnight Cowboy",
        ),
        ("enfants-riches-deprimes", "belt", "ERD Rose Buckle Studded Belt", "Rose"),
    ],
)
def test_a_liquid_line_reaches_its_seed(
    policy: GroupingPolicy, slug: str, product_type: str, title: str, expected: str
) -> None:
    assert _model(policy, slug, product_type, title) == expected


@pytest.mark.parametrize(
    ("slug", "product_type", "title", "mark"),
    [
        # "Maison Martin" was mined as a model in 8 types: it is the house name.
        ("maison-margiela", "tshirt", "SS16 Maison Martin Margiela Blue Basic T Shirt", "maison"),
        ("maison-margiela", "pants", "Martin Margiela FW05 Drawstring Military Pants", "martin"),
        ("golden-goose", "lowtop_sneakers", "Golden Goose Deluxe Brand White Sneakers", "deluxe"),
    ],
)
def test_house_names_are_brand_marks_not_models(
    policy: GroupingPolicy, slug: str, product_type: str, title: str, mark: str
) -> None:
    tokens = _normalizer(policy, slug).normalize(title).tokens

    assert _model(policy, slug, product_type, title) is None
    found = policy.descriptor_index(slug).find(product_type, tokens)
    assert found is not None and mark in found.descriptor.phrases[0]


def test_a_bare_star_is_no_golden_goose_model(policy: GroupingPolicy) -> None:
    assert _model(policy, "golden-goose", "hoodie", "Golden Goose star zip track hoodie") is None


@pytest.mark.parametrize(
    ("slug", "product_type", "title", "expected"),
    [
        (
            "maison-margiela",
            "hat",
            "Supreme MM6 Maison Margiela Split 6-Panel Hat Purple",
            "Supreme",
        ),
        ("maison-margiela", "lowtop_sneakers", "Salomon MM6 Maison Margiela ACS Pro", "Salomon"),
        (
            "maison-margiela",
            "lowtop_sneakers",
            'Club C "Memory Of" Reebok Maison Margiela',
            "Reebok",
        ),
        ("maison-margiela", "boots", "Maison Martin Margiela x H&M 2012 Red High-Top", "H&M"),
        ("maison-margiela", "hitop_sneakers", "Maison Margiela x Converse Chuck 70", "Converse"),
        (
            "enfants-riches-deprimes",
            "sunglasses",
            "Enfants Riches Deprimes JMM Mulholland",
            "Jacques Marie Mage",
        ),
    ],
)
def test_collaborations_get_their_line(
    policy: GroupingPolicy, slug: str, product_type: str, title: str, expected: str
) -> None:
    normalizer = _normalizer(policy, slug)
    index = CollabIndex(policy.brand_collabs(slug), normalizer.spelled)
    found = index.find(normalizer.normalize(title).spelled, product_type)
    assert (found.name if found else None) == expected


@pytest.mark.parametrize("slug", sorted(BRANDS))
def test_no_collaboration_or_mark_eats_a_seed_name(policy: GroupingPolicy, slug: str) -> None:
    normalizer = _normalizer(policy, slug)
    for seed in policy.seed_models(slug):
        for alias in seed.aliases:
            assert normalizer.phrase(alias), (slug, alias)
