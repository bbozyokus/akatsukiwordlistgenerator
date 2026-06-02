# Akatsuki Wordlist Generator

A targeted password wordlist generator for corporate penetration testing and offline hash cracking.

Given a company name, keywords, employee names, and a year range, the tool produces case variations, leet speak substitutions, special-character mutations, year affixes, word combinations, and chained mutations. It streams results directly to disk, keeping memory usage constant regardless of wordlist size.

## Requirements

- Python 3.10 or later
- PyYAML (`pip install pyyaml`)

## Quick Start

```bash
pip install pyyaml
python akatsukiwordlistgen.py --company AcmeCorp --keywords api,prod --year-range 2020-2024 --leet --special-chars
```

## Command-Line Options

| Flag | Description |
|---|---|
| `--company` | Company name (required) |
| `--keywords` | Comma-separated keywords (products, projects, cities) |
| `--names` | Comma-separated employee names |
| `--year-range` | Year range as `START-END` (e.g. `2020-2024`) |
| `--leet` | Enable leet speak mutations (a->@/4, e->3, i->1, o->0, s->$/5) |
| `--special-chars` | Append/prepend special-character combinations |
| `--min-length` / `--max-length` | Filter candidates by length |
| `--output` | Output filename (default: `CompanyName-wordlist.lst`) |
| `--profile` | Load settings from a YAML profile file |
| `--no-dedup` | Skip in-memory deduplication (use for very large wordlists) |
| `--no-leet` / `--no-special-chars` | Explicitly disable those mutation types |

Run with no arguments to enter interactive mode.

## YAML Profiles

Save and reuse configurations:

```bash
# After generation you are prompted to save a profile,
# or load one with --profile:
python akatsukiwordlistgen.py --profile my-org.akatsuki.yaml --output crackme.lst
```

## Mutation Pipeline

1. **Base-word extraction** — company name, keywords, employee names, camelCase splits, initials
2. **Case variations** — lower, UPPER, Title, camelCase
3. **Leet speak** — character-level substitutions (per-character Cartesian product)
4. **Year affixes** — prepend/append each year with separators (e.g. `acme2024`, `2024!acme`)
5. **Special characters** — combinations of 1-3 special chars appended/prepended
6. **Word combinations** — 2-word and 3-word joins with 15 different separators
7. **Chained mutations** — year + special-char mutations applied to primary outputs
8. **Length filtering** — optional min/max length constraints
9. **Deduplication** — enabled by default; disable with `--no-dedup`

## Memory Behaviour

The pipeline is fully lazy (generator-based). Candidates are written to disk as they are produced; peak memory usage stays constant. For extremely large lists, passing `--no-dedup` eliminates the deduplication set entirely.

## License

This tool is provided for authorised security testing and research purposes only.