"""
Akatsuki Wordlist Generator

A Python CLI tool for corporate penetration testing that generates targeted
password wordlists based on company information, keywords, and employee names.
Supports leet speak substitutions, case variations, year appending/prepending,
special character mutations, word combinations, and mutation chaining.
Configuration can be loaded from and saved to YAML profile files.
Supports both interactive and parameterized CLI modes.
"""

import argparse
import itertools
import os
import re
import sys
import yaml

# Leet speak character substitution map.
# Only vowels are substituted — these are the swaps real users actually make
# (a→@/4, e→3, i→1, o→0). Consonant leet (e.g. s→$/5) was removed as unrealistic.
LEET_MAP = {
    'a': ['@', '4'],
    'e': ['3'],
    'i': ['1'],
    'o': ['0'],
}

# Special characters people actually append/prepend to passwords.
# '!' and '_' first because they are by far the most common real-world choices.
SPECIAL_CHARS = ['!', '_', '.', '@', '#', '*', '?', '-']

# Maximum length of special character combinations (1 to MAX_SPECIAL_COMBO_LEN).
# Kept at 2 so things like "!" and "!!" are covered without exploding into
# unrealistic 3-char runs of random symbols.
MAX_SPECIAL_COMBO_LEN = 2

# Separators for joining words in combinations — only the ones real users type.
SEPARATORS = ['', '_', '.', '-', '!', '@']


def parse_cli_args() -> argparse.Namespace:
    """Parse command-line arguments using argparse.

    Returns:
        argparse.Namespace: Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser(
        description="Akatsuki Wordlist Generator - Generate targeted password wordlists for corporate penetration testing."
    )

    parser.add_argument(
        "--company",
        type=str,
        help="Company name (required unless provided via profile or interactive mode)",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Company founding year",
    )
    parser.add_argument(
        "--year-range",
        type=str,
        help="Year range for year mutations in START-END format (e.g. 2020-2024)",
    )
    parser.add_argument(
        "--keywords",
        type=str,
        help="Comma-separated list of keywords (project names, product names, city names)",
    )
    parser.add_argument(
        "--names",
        type=str,
        help="Comma-separated list of employee names (first last)",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        help="Minimum password length filter",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        help="Maximum password length filter",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output filename for the generated wordlist",
    )
    parser.add_argument(
        "--profile",
        type=str,
        help="Path to a YAML profile file to load settings from",
    )
    parser.add_argument(
        "--leet",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable leet speak mutations (a→@/4, e→3, i→1, o→0, s→$/5)",
    )
    parser.add_argument(
        "--special-chars",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable special character mutations (multi-char combos appended/prepended/between word and year)",
    )
    parser.add_argument(
        "--max-count",
        type=int,
        help="Stop after generating this many candidates (safety cap against combinatorial explosion)",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        default=False,
        help="Skip in-memory deduplication (stream directly to file, "
             "useful for very large wordlists where memory is a constraint)",
    )

    return parser.parse_args()


def resolve_config(args: argparse.Namespace) -> dict:
    """Merge CLI args, profile, and interactive input into final config.

    Priority: CLI args > profile > interactive prompts.

    1. Build config from CLI arguments.
    2. If --profile is specified, load that profile; otherwise call
       find_default_profile() to look for a default.
    3. If a profile is found, merge profile values into config (CLI args
       take priority over profile values).
    4. If company name is still missing after CLI+profile merge, fall back
       to interactive mode.
    5. If running non-interactively (e.g., piped input) and company is
       missing, print error and exit.

    Args:
        args: Parsed CLI arguments from argparse.

    Returns:
        dict: Configuration dictionary with standardized keys.
    """
    # --- Step 1: Build config from CLI args ---
    keywords = []
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    names = []
    if args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]

    year_range = None
    if args.year_range:
        parts = args.year_range.split("-")
        if len(parts) == 2:
            year_range = (int(parts[0]), int(parts[1]))

    config = {
        "company": args.company,
        "founding_year": args.year,
        "keywords": keywords,
        "names": names,
        "year_range": year_range,
        "min_length": args.min_length,
        "max_length": args.max_length,
        "output": args.output,
        "max_count": getattr(args, "max_count", None),
        "leet": getattr(args, "leet", None),
        "special_chars": getattr(args, "special_chars", None),
    }

    # --- Step 2: Load profile ---
    profile_config = None
    profile_path = getattr(args, "profile", None)
    if profile_path:
        profile_config = load_profile(profile_path)
    else:
        default_path = find_default_profile()
        if default_path:
            profile_config = load_profile(default_path)

    # --- Step 3: Merge profile values (CLI args take priority) ---
    if profile_config is not None:
        for key in config:
            cli_value = config[key]
            # Treat None and empty lists as "not provided by CLI"
            if cli_value is None or (isinstance(cli_value, list) and len(cli_value) == 0):
                profile_value = profile_config.get(key)
                if profile_value is not None:
                    config[key] = profile_value

    # --- Step 4/5: Interactive fallback or error if company still missing ---
    if not config.get("company"):
        if sys.stdin.isatty():
            interactive_config = run_interactive_mode()
            for key in config:
                cli_value = config[key]
                if cli_value is None or (isinstance(cli_value, list) and len(cli_value) == 0):
                    interactive_value = interactive_config.get(key)
                    if interactive_value is not None:
                        config[key] = interactive_value
        else:
            print("Error: --company is required when running non-interactively.", file=sys.stderr)
            sys.exit(1)

    return config


def run_interactive_mode() -> dict:
    """Prompt user for all parameters interactively.

    Collects company name (required), founding year, keywords, employee names,
    year range, min/max length, and output filename via interactive prompts.
    All prompts are displayed in English.

    Returns:
        dict: Configuration dictionary with the same keys as resolve_config.
    """
    print("=== Akatsuki Wordlist Generator - Interactive Mode ===\n")

    # Company name (required)
    company = ""
    while not company.strip():
        company = input("Enter company name (required): ").strip()
        if not company:
            print("Company name is required. Please try again.")

    # Founding year (optional)
    founding_year = None
    year_input = input("Enter founding year (optional, press Enter to skip): ").strip()
    if year_input:
        try:
            founding_year = int(year_input)
        except ValueError:
            print("Invalid year format. Skipping founding year.")

    # Keywords (optional)
    keywords_input = input("Enter keywords, comma-separated (optional, press Enter to skip): ").strip()
    keywords = [k.strip() for k in keywords_input.split(",") if k.strip()] if keywords_input else []

    # Employee names (optional, ask whether to include first)
    names = []
    include_names = input("Include employee names in wordlist generation? (y/n): ").strip().lower()
    if include_names in ("y", "yes"):
        names_input = input("Enter employee names, comma-separated (optional, press Enter to skip): ").strip()
        names = [n.strip() for n in names_input.split(",") if n.strip()] if names_input else []

    # Year range (optional)
    year_range = None
    year_range_input = input("Enter year range as START-END (optional, press Enter to skip): ").strip()
    if year_range_input:
        parts = year_range_input.split("-")
        if len(parts) == 2:
            try:
                year_range = (int(parts[0].strip()), int(parts[1].strip()))
            except ValueError:
                print("Invalid year range format. Skipping year range.")
        else:
            print("Invalid year range format. Use START-END (e.g. 2020-2024). Skipping.")

    # Minimum length (optional)
    min_length = None
    min_input = input("Enter minimum password length (optional, press Enter to skip): ").strip()
    if min_input:
        try:
            min_length = int(min_input)
        except ValueError:
            print("Invalid number. Skipping minimum length.")

    # Maximum length (optional)
    max_length = None
    max_input = input("Enter maximum password length (optional, press Enter to skip): ").strip()
    if max_input:
        try:
            max_length = int(max_input)
        except ValueError:
            print("Invalid number. Skipping maximum length.")

    # Output filename (optional)
    output = None
    output_input = input("Enter output filename (optional, press Enter for default): ").strip()
    if output_input:
        output = output_input

    # Leet speak mutations
    leet = False
    leet_input = input("Enable leet speak mutations? (y/n, default: n): ").strip().lower()
    if leet_input in ("y", "yes"):
        leet = True

    # Special character mutations
    special_chars = False
    sc_input = input("Enable special character mutations? (y/n, default: n): ").strip().lower()
    if sc_input in ("y", "yes"):
        special_chars = True

    config = {
        "company": company,
        "founding_year": founding_year,
        "keywords": keywords,
        "names": names,
        "year_range": year_range,
        "min_length": min_length,
        "max_length": max_length,
        "output": output,
        "max_count": None,
        "leet": leet,
        "special_chars": special_chars,
    }

    return config


# Default profile filename patterns (Requirement 12.1)
DEFAULT_PROFILE_NAMES = ["akatsuki-profile.yaml", "akatsuki-profile.yml"]
DEFAULT_PROFILE_GLOB_SUFFIX = ".akatsuki.yaml"


def find_default_profile() -> str | None:
    """Search the current directory for a default YAML profile file.

    Looks for files named 'akatsuki-profile.yaml', 'akatsuki-profile.yml',
    or any file matching '*.akatsuki.yaml' in the current directory.

    Returns:
        str | None: Path to the found profile file, or None if not found.
    """
    cwd = os.getcwd()

    # Check well-known filenames first
    for name in DEFAULT_PROFILE_NAMES:
        candidate = os.path.join(cwd, name)
        if os.path.isfile(candidate):
            return candidate

    # Check for *.akatsuki.yaml pattern
    for entry in os.listdir(cwd):
        if entry.endswith(DEFAULT_PROFILE_GLOB_SUFFIX) and os.path.isfile(os.path.join(cwd, entry)):
            return os.path.join(cwd, entry)

    return None


def load_profile(path: str) -> dict:
    """Load a YAML profile file and return a config dictionary.

    Reads the YAML file at the given path and converts it into the internal
    config dict format. Handles year_range conversion from dict {start, end}
    to tuple (start, end).

    Args:
        path: Path to the YAML profile file.

    Returns:
        dict: Configuration dictionary with standardized keys.

    Raises:
        FileNotFoundError: If the specified profile file does not exist.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Profile file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Convert year_range from dict to tuple
    year_range = None
    yr = data.get("year_range")
    if isinstance(yr, dict) and "start" in yr and "end" in yr:
        year_range = (yr["start"], yr["end"])

    config = {
        "company": data.get("company"),
        "founding_year": data.get("founding_year"),
        "keywords": data.get("keywords", []),
        "names": data.get("names", []),
        "year_range": year_range,
        "min_length": data.get("min_length"),
        "max_length": data.get("max_length"),
        "output": data.get("output"),
        "max_count": data.get("max_count"),
        "leet": data.get("leet"),
        "special_chars": data.get("special_chars"),
    }

    return config


def save_profile(config: dict, path: str) -> None:
    """Save the current config dictionary to a YAML profile file.

    Converts the internal config format (year_range as tuple) to the YAML
    schema format (year_range as dict with start/end keys).

    Args:
        config: Configuration dictionary to save.
        path: Path to write the YAML profile file.
    """
    data = {
        "company": config.get("company"),
        "founding_year": config.get("founding_year"),
        "keywords": config.get("keywords", []),
        "names": config.get("names", []),
        "year_range": None,
        "min_length": config.get("min_length"),
        "max_length": config.get("max_length"),
        "output": config.get("output"),
        "max_count": config.get("max_count"),
        "leet": config.get("leet", False),
        "special_chars": config.get("special_chars", False),
    }

    # Convert year_range tuple to dict for YAML
    yr = config.get("year_range")
    if yr is not None and isinstance(yr, (list, tuple)) and len(yr) == 2:
        data["year_range"] = {"start": yr[0], "end": yr[1]}

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _split_camel_case(word: str) -> list:
    """Split a CamelCase or mixed-case word into its component parts.

    Examples:
        'AcmeCorp' -> ['Acme', 'Corp']
        'HTTPServer' -> ['HTTP', 'Server']
        'myProject' -> ['my', 'Project']
        'simple' -> ['simple']
    """
    parts = re.sub(r'([a-zçğıöşü])([A-ZÇĞİÖŞÜ])', r'\1 \2', word).split()
    return [p for p in parts if p]


def collect_base_words(config: dict) -> list:
    """Extract all base words from config: company name, keywords, employee names.

    For the company name, generates additional variants:
    - CamelCase split parts (e.g., 'AcmeCorp' -> 'Acme', 'Corp')
    - Abbreviation/initials from CamelCase parts (e.g., 'AcmeCorp' -> 'AC')
    - Separator-split parts (e.g., 'acme-corp' -> 'acme', 'corp')
    - No-space/no-separator concatenation of parts

    Words are never character-reversed (e.g. 'abc' never yields 'cba'), since
    real users do not reverse their company or personal names when choosing
    passwords.

    Collects the company name, all keywords, and for each employee name the
    full name, first name, and last name. Returns a deduplicated list that
    preserves insertion order. Supports Turkish characters in all inputs.

    Args:
        config: Configuration dictionary with keys 'company', 'keywords', 'names'.

    Returns:
        list: Deduplicated list of base words.
    """
    seen = set()
    base_words = []

    def _add(word):
        if word and word not in seen:
            seen.add(word)
            base_words.append(word)

    def _expand_word(word):
        """Generate extra base words from a compound word."""
        # Only add the original if it has no spaces (passwords don't have spaces)
        if ' ' not in word:
            _add(word)

        # Split on separators (space, _, -, .)
        sep_parts = re.split(r'[\s_.\-]+', word)
        sep_parts = [p for p in sep_parts if p]

        # CamelCase split
        camel_parts = _split_camel_case(word)

        # Use whichever split produced more parts
        parts = camel_parts if len(camel_parts) > len(sep_parts) else sep_parts

        if len(parts) >= 2:
            for p in parts:
                _add(p)
            # Initials / abbreviation
            initials = "".join(p[0] for p in parts if p)
            _add(initials.upper())
            _add(initials.lower())
            # Concatenated without separator
            _add("".join(p.lower() for p in parts))

    # Company name — expand with extra variants
    company = config.get("company")
    if company:
        _expand_word(company)

    # Keywords — also expand compound keywords
    for kw in config.get("keywords", []):
        _expand_word(kw)

    # Employee names: first name, last name, initials, concatenated
    for name in config.get("names", []):
        parts = name.split()
        if len(parts) >= 2:
            _add(parts[0])   # first name
            _add(parts[-1])  # last name
            # Name initials
            initials = "".join(p[0] for p in parts if p)
            _add(initials.lower())
            _add(initials.upper())
            # First + last concatenated (natural order only; no last+first,
            # which would be an unrealistic reversed ordering)
            _add(parts[0].lower() + parts[-1].lower())
        else:
            _add(name)  # single-word name, no spaces

    return base_words


# Regex pattern matching any separator used for multi-word detection
_SEPARATOR_RE = re.compile(r'[\s_.\-@]+')


def generate_case_variants(word: str) -> list:
    """Generate case variations: lowercase, uppercase, title case, camelCase.

    camelCase is only produced for multi-word inputs where words are separated
    by spaces or separators (_, ., -, @). For camelCase the first word is
    lowercased and subsequent words are title-cased, then all are joined.

    Args:
        word: The input word to generate case variants for.

    Returns:
        list: Deduplicated list of case variants.
    """
    seen = set()
    variants = []

    def _add(v):
        if v not in seen:
            seen.add(v)
            variants.append(v)

    _add(word.lower())
    _add(word.upper())
    _add(word.title())

    # camelCase for multi-word inputs
    parts = _SEPARATOR_RE.split(word)
    parts = [p for p in parts if p]  # drop empty strings from split
    if len(parts) >= 2:
        camel = parts[0].lower() + "".join(p.title() for p in parts[1:])
        _add(camel)

    return variants


def generate_leet_variants(word: str) -> list:
    """Generate ALL leet speak combinations for a word.

    For each character in the word, if its lowercase form is in LEET_MAP,
    the options are [original_char] + LEET_MAP substitutions. Otherwise the
    option is just [original_char]. Uses itertools.product over per-character
    option lists to produce every combination. The original word is included
    as one of the combinations (where no substitution is applied).

    Args:
        word: The input word to generate leet variants for.

    Returns:
        list: Deduplicated list of all leet speak variants including the original.
    """
    char_options = []
    for ch in word:
        lower_ch = ch.lower()
        if lower_ch in LEET_MAP:
            char_options.append([ch] + LEET_MAP[lower_ch])
        else:
            char_options.append([ch])

    seen = set()
    variants = []
    for combo in itertools.product(*char_options):
        variant = "".join(combo)
        if variant not in seen:
            seen.add(variant)
            variants.append(variant)

    return variants


# Separators used between word and year (includes direct concat) — realistic set.
YEAR_SEPARATORS = ['', '.', '_', '-', '!']


def generate_year_variants(word: str, start_year: int, end_year: int) -> list:
    """Append and prepend each year in range to word with various separators.

    For each year in range and each separator, produces:
    - Appended: word + sep + year (e.g., "acme.2020", "acme_2020", "acme2020")
    - Prepended: year + sep + word (e.g., "2020.acme", "2020_acme", "2020acme")

    Args:
        word: The input word to generate year variants for.
        start_year: The first year in the range (inclusive).
        end_year: The last year in the range (inclusive).

    Returns:
        list: List of all year variants with separators.
    """
    variants = []
    for year in range(start_year, end_year + 1):
        year_str = str(year)
        for sep in YEAR_SEPARATORS:
            variants.append(word + sep + year_str)
            variants.append(year_str + sep + word)
    return variants


def _build_special_combos(chars: list, max_len: int) -> list:
    """Build ALL special character combinations from length 1 to max_len.

    Uses itertools.product to generate every possible combination with repetition
    for each length from 1 to max_len.

    Returns:
        list: Deduplicated list of character combination strings.
    """
    seen = set()
    combos = []

    def _add(c):
        if c not in seen:
            seen.add(c)
            combos.append(c)

    for length in range(1, max_len + 1):
        for combo in itertools.product(chars, repeat=length):
            _add("".join(combo))

    return combos


def generate_special_char_variants(word: str) -> list:
    """Append and prepend special character combinations to word.

    Generates combinations of 1 to MAX_SPECIAL_COMBO_LEN special characters
    and appends/prepends each to the word.

    Args:
        word: The input word to generate special character variants for.

    Returns:
        list: List of all special-character variants.
    """
    combos = _build_special_combos(SPECIAL_CHARS, MAX_SPECIAL_COMBO_LEN)
    variants = []
    for combo in combos:
        variants.append(word + combo)
        variants.append(combo + word)
    return variants


def generate_word_combinations(base_words: list):
    """Generate 2-word and 3-word combinations from base words using all separators.

    Combinations keep the natural left-to-right order of base_words (via
    itertools.combinations_with_replacement). Reversed orderings such as
    "corp_acme" for an "Acme Corp" input are NOT produced, because real users do
    not reverse the order of their own company/keyword/name parts when choosing a
    password. Same-word pairs (e.g. "acme_acme") are still included.

    Uses itertools.combinations_with_replacement to produce natural-order
    combinations including same-word pairs (e.g., word1!word1), but never the
    reversed order. Each combination is joined with every separator in
    SEPARATORS. This is a generator — it yields results lazily to avoid building
    a huge list in memory.

    Args:
        base_words: List of base words to combine.

    Yields:
        str: Combination variants joined with each separator.
    """
    if len(base_words) < 1:
        return

    # 2-word combinations (natural order, with repetition — allows word1+word1)
    for combo in itertools.combinations_with_replacement(base_words, 2):
        for sep in SEPARATORS:
            yield sep.join(combo)

    # 3-word combinations (natural order) — only if at least 2 base words
    if len(base_words) >= 2:
        for combo in itertools.combinations_with_replacement(base_words, 3):
            for sep in SEPARATORS:
                yield sep.join(combo)


def apply_chained_mutations(
    primary_outputs,
    start_year: int | None,
    end_year: int | None,
    use_special_chars: bool = True,
):
    """Apply year and special char mutations on top of primary mutation outputs.

    For each word in primary_outputs:
    - If a year range is provided, generate year variants (word+sep+year)
    - If special chars enabled, generate special char variants (word+chars, chars+word)
    - If both, generate word+chars+year patterns (e.g., acme!2024, acme!!2024)

    This is a generator — it yields results lazily to avoid building a huge
    list in memory. primary_outputs can be any iterable.

    Args:
        primary_outputs: Iterable of strings from case variations, leet speak,
            and word combination outputs.
        start_year: Start of year range (inclusive), or None if no year range.
        end_year: End of year range (inclusive), or None if no year range.
        use_special_chars: Whether to apply special character mutations.

    Yields:
        str: Chained mutation results.
    """
    has_year_range = start_year is not None and end_year is not None
    combos = _build_special_combos(SPECIAL_CHARS, MAX_SPECIAL_COMBO_LEN) if use_special_chars else []

    for word in primary_outputs:
        if has_year_range:
            yield from generate_year_variants(word, start_year, end_year)
        if use_special_chars:
            yield from generate_special_char_variants(word)
        # word + chars + year (e.g., acme!2024, acme!!2024)
        if has_year_range and use_special_chars:
            for combo in combos:
                for year in range(start_year, end_year + 1):
                    year_str = str(year)
                    yield word + combo + year_str
                    yield year_str + combo + word


def filter_by_length(candidates, min_len=None, max_len=None):
    """Filter candidates by min/max length constraints (generator).

    Args:
        candidates: An iterable of strings to filter.
        min_len: Minimum password length (inclusive), or None for no minimum.
        max_len: Maximum password length (inclusive), or None for no maximum.

    Yields:
        str: Candidates that satisfy the length constraints.
    """
    if min_len is None and max_len is None:
        yield from candidates
        return

    for candidate in candidates:
        length = len(candidate)
        if min_len is not None and length < min_len:
            continue
        if max_len is not None and length > max_len:
            continue
        yield candidate


def estimate_wordlist_size(config: dict, base_words: list | None = None) -> tuple:
    """Estimate the number of candidates and file size before generating.

    Calculates approximate counts based on base words, enabled mutations,
    year range, and special char combos without actually generating the wordlist.

    Args:
        config: Configuration dictionary.
        base_words: Optional pre-computed base words list. If not provided,
            collect_base_words() is called (which adds overhead).

    Returns:
        tuple: (estimated_count, estimated_bytes, base_word_count)
    """
    if base_words is None:
        base_words = collect_base_words(config)
    n = len(base_words)
    if n == 0:
        return (0, 0, 0)

    year_range = config.get("year_range")
    num_years = (year_range[1] - year_range[0] + 1) if year_range else 0
    use_leet = config.get("leet", False)
    use_special = config.get("special_chars", False)
    num_seps = len(SEPARATORS)
    num_year_seps = len(YEAR_SEPARATORS)
    num_special_combos = len(_build_special_combos(SPECIAL_CHARS, MAX_SPECIAL_COMBO_LEN)) if use_special else 0

    # Average leet variants per word (rough estimate: ~4x for typical words)
    avg_leet_per_word = 4 if use_leet else 0
    # Case variants per word (typically 3-4)
    avg_case_per_word = 3

    # --- Per base word counts ---
    per_word = 1  # base word itself
    per_word += avg_case_per_word  # case variants
    per_word += avg_leet_per_word  # leet variants
    if num_years > 0:
        per_word += num_years * num_year_seps * 2  # year variants (append+prepend per sep)
    if use_special:
        per_word += num_special_combos * 2  # special char variants (append+prepend)

    individual_total = n * per_word

    # --- Word combinations ---
    combo_2word = (n ** 2) * num_seps
    combo_3word = (n ** 3) * num_seps if n >= 2 else 0
    combo_total = combo_2word + combo_3word

    # --- Primary outputs for chaining ---
    primary_per_word = avg_case_per_word + avg_leet_per_word
    primary_total = n * primary_per_word + combo_total

    # --- Chained mutations ---
    chained = 0
    if num_years > 0:
        chained += primary_total * num_years * num_year_seps * 2
    if use_special:
        chained += primary_total * num_special_combos * 2
    if num_years > 0 and use_special:
        chained += primary_total * num_special_combos * num_years * 2  # word+chars+year

    total_before_dedup = individual_total + combo_total + chained
    # Rough dedup factor (typically 70-90% unique)
    estimated_count = int(total_before_dedup * 0.80)

    # Average candidate length estimate (base word avg + extras)
    avg_word_len = sum(len(w) for w in base_words) / n if n > 0 else 8
    avg_candidate_len = avg_word_len + 3  # average extra chars from mutations
    estimated_bytes = int(estimated_count * (avg_candidate_len + 1))  # +1 for newline

    return (estimated_count, estimated_bytes, n)


def _format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} bytes"


def generate_wordlist_stream(config: dict, base_words: list, dedup: bool = True):
    """Main pipeline as a generator: collect base words, apply all mutations,
    chain mutations, filter, and optionally deduplicate — all lazily.

    Instead of building a giant list in memory, this yields candidates one
    by one as they are produced. When dedup=True, an internal `seen` set is
    maintained to skip duplicates. When dedup=False, every candidate is
    yielded as-is (useful for very large wordlists where the dedup set
    itself would exhaust memory).

    Pipeline steps:
    1. Yield base words
    2. For each base word, yield individual mutations (case, leet, year, special char)
    3. Yield word combinations (lazy generator)
    4. Collect primary outputs (case + leet + combos) for chaining (replayed)
    5. Yield chained mutations (lazy generator)
    6. Filter by length (lazy)
    7. Optionally deduplicate (lazy, with `seen` set if dedup=True)

    Args:
        config: Configuration dictionary.
        base_words: Pre-computed list of base words.
        dedup: If True, skip duplicates via an in-memory set.
               If False, yield everything (no memory for dedup set).

    Yields:
        str: Password candidates.
    """
    year_range = config.get("year_range")
    start_year = year_range[0] if year_range else None
    end_year = year_range[1] if year_range else None
    min_len = config.get("min_length")
    max_len = config.get("max_length")
    use_leet = config.get("leet", False)
    use_special = config.get("special_chars", False)

    # ---- Helper: unified yield with length filter and optional dedup ----
    seen = set() if dedup else None

    def _emit(candidate):
        """Check length, check dedup, and yield if passes."""
        length = len(candidate)
        if min_len is not None and length < min_len:
            return
        if max_len is not None and length > max_len:
            return
        if seen is not None:
            if candidate in seen:
                return
            seen.add(candidate)
        return True

    def _emit_and_yield(candidate):
        if _emit(candidate) is True:
            return candidate
        return None

    # ---- Phase 1: Base words ----
    for word in base_words:
        result = _emit_and_yield(word)
        if result is not None:
            yield result

    # ---- Phase 2: Individual mutations (case, leet, year, special char) ----
    # Replay buffer: collect primary outputs so chained mutations can iterate
    # them a second time (but only the lightweight case+leet+combos).
    # We DON'T materialize the whole wordlist — just the primary outputs which
    # are case+leet variants + combos (still large but orders of magnitude
    # smaller than the final chained fan-out).
    primary_outputs = []

    for word in base_words:
        # Case variants
        for case_var in generate_case_variants(word):
            result = _emit_and_yield(case_var)
            if result is not None:
                primary_outputs.append(result)
                yield result

        # Leet speak variants
        if use_leet:
            for leet_var in generate_leet_variants(word):
                result = _emit_and_yield(leet_var)
                if result is not None:
                    primary_outputs.append(result)
                    yield result

        # Year variants (individual, directly on base words)
        if start_year is not None and end_year is not None:
            for year_var in generate_year_variants(word, start_year, end_year):
                result = _emit_and_yield(year_var)
                if result is not None:
                    yield result

        # Special char variants (individual, directly on base words)
        if use_special:
            for sc_var in generate_special_char_variants(word):
                result = _emit_and_yield(sc_var)
                if result is not None:
                    yield result

    # ---- Phase 3: Word combinations ----
    for combo in generate_word_combinations(base_words):
        result = _emit_and_yield(combo)
        if result is not None:
            primary_outputs.append(result)
            yield result

    # ---- Phase 4: Chained mutations on primary outputs ----
    for chained in apply_chained_mutations(primary_outputs, start_year, end_year, use_special):
        result = _emit_and_yield(chained)
        if result is not None:
            yield result


def generate_wordlist(config: dict) -> list:
    """Main pipeline: collect base words, apply all mutations, chain mutations,
    filter, deduplicate. Returns a deduplicated list of candidates.

    NOTE: This function is kept for backward compatibility but will consume
    substantial memory for large wordlists. For large generation, use
    generate_wordlist_stream() directly from main().

    Pipeline steps:
    1. Collect base words from config
    2. For each base word, generate individual mutations
    3. Generate word combinations
    4. Apply chained mutations on primary outputs
    5. Filter by length
    6. Deduplicate
    7. Return the final list

    Args:
        config: Configuration dictionary.

    Returns:
        list: Deduplicated list of password candidates.
    """
    base_words = collect_base_words(config)
    # Stream to list (still builds in memory, but at least doesn't duplicate
    # the intermediate all_candidates + primary_outputs lists)
    return list(generate_wordlist_stream(config, base_words, dedup=True))


def get_default_filename(company_name: str) -> str:
    """Return the default output filename for a given company name.

    Args:
        company_name: The company name to use in the filename.

    Returns:
        str: Default filename in the format '{company_name}-wordlist.lst'.
    """
    return f"{company_name}-wordlist.lst"


def write_wordlist(candidates, output_path: str) -> int:
    """Write all password candidates to a file, one per line, UTF-8 encoded.

    Args:
        candidates: An iterable of password candidate strings.
        output_path: Path to the output file.

    Returns:
        int: The number of candidates written.
    """
    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for candidate in candidates:
            f.write(candidate + "\n")
            count += 1
    return count


def progress_indicator(current: int, total: int) -> None:
    """Display a progress bar with percentage to stderr.

    Uses carriage return to overwrite the line in-place. The bar is 30
    characters wide and shows the current/total count alongside a percentage.

    Args:
        current: The current progress count.
        total: The total expected count.
    """
    if total <= 0:
        return
    fraction = current / total
    percent = int(fraction * 100)
    bar_width = 30
    filled = int(bar_width * fraction)
    bar = "=" * filled
    if filled < bar_width:
        bar += ">"
    bar = bar.ljust(bar_width)
    sys.stderr.write(f"\r[{bar}] {percent}% ({current}/{total})")
    sys.stderr.flush()
    if current >= total:
        sys.stderr.write("\n")
        sys.stderr.flush()


def print_summary(count: int, output_path: str) -> None:
    """Print a summary of the wordlist generation to stdout.

    Displays the total number of password candidates, the output file path,
    and the file size in a human-readable format.

    Args:
        count: Total number of password candidates written.
        output_path: Path to the output wordlist file.
    """
    file_size = os.path.getsize(output_path)

    # Format file size for readability
    if file_size >= 1024 * 1024:
        size_str = f"{file_size / (1024 * 1024):.2f} MB"
    elif file_size >= 1024:
        size_str = f"{file_size / 1024:.2f} KB"
    else:
        size_str = f"{file_size} bytes"

    print(f"\n=== Wordlist Generation Summary ===")
    print(f"Total candidates: {count}")
    print(f"Output file:      {output_path}")
    print(f"File size:        {size_str}")


def main() -> None:
    """Entry point: resolve config, generate wordlist, write output, show summary.

    Orchestrates the full pipeline:
    1. Parse CLI arguments
    2. Resolve configuration (CLI > profile > interactive fallback)
    3. Estimate wordlist size and ask for confirmation
    4. Generate the wordlist (streaming, lazy)
    5. Write candidates to file with progress % (against estimate)
    6. Print summary
    7. Optionally save settings as a YAML profile
    """
    args = parse_cli_args()
    config = resolve_config(args)

    # Determine output filename
    output_path = config.get("output") or get_default_filename(config["company"])

    # Validate that the output directory exists before doing any work
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(out_dir):
        print(f"Error: output directory does not exist: {out_dir}", file=sys.stderr)
        sys.exit(1)

    # Collect base words ONCE and reuse for both estimation and generation
    base_words = collect_base_words(config)

    # --- Step 3: Estimate BEFORE generating (reuse base_words) ---
    est_count, est_bytes, base_count = estimate_wordlist_size(config, base_words=base_words)
    no_dedup = getattr(args, "no_dedup", False)
    max_count = config.get("max_count")
    if max_count is not None and max_count <= 0:
        print("Error: --max-count must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    # A max-count cap also caps the effective estimate used for progress/size
    if max_count is not None and est_count > max_count:
        avg_bytes_per = (est_bytes / est_count) if est_count > 0 else 0
        est_count = max_count
        est_bytes = int(max_count * avg_bytes_per)

    print(f"\n=== Wordlist Estimation ===")
    print(f"Base words:              {base_count}")
    print(f"Estimated candidates:    ~{est_count:,}")
    print(f"Estimated file size:     ~{_format_size(est_bytes)}")
    print(f"Output file:             {output_path}")
    if max_count is not None:
        print(f"Max count:               {max_count:,} (hard cap)")
    if no_dedup:
        print(f"Deduplication:           DISABLED (--no-dedup)")

    # Ask for confirmation before generating (only if stdin is interactive)
    try:
        if sys.stdin.isatty():
            confirm = input("\nProceed with generating the wordlist? (y/n): ").strip().lower()
            if confirm not in ("y", "yes", ""):
                print("Wordlist generation cancelled.")
                return
    except (EOFError, KeyboardInterrupt, OSError):
        pass  # Non-interactive or broken stdin — proceed

    # --- Step 4 & 5: Generate and write wordlist (streaming) ---
    print("\nGenerating wordlist...")
    print(f"  (Deduplication: {'OFF' if no_dedup else 'ON'})")
    print(f"  Streaming to: {output_path}")

    count = 0
    # Use estimate as total for progress bar
    total = est_count if est_count > 0 else 1

    with open(output_path, "w", encoding="utf-8") as f:
        for candidate in generate_wordlist_stream(config, base_words, dedup=not no_dedup):
            f.write(candidate + "\n")
            count += 1
            # Update progress every 1000 candidates (avoid excessive stderr writes)
            if count % 1000 == 0 or count == 1:
                # Show progress relative to estimate, capped at 99% until done
                display_current = min(count, total)
                progress_indicator(display_current, total)
            # Enforce the safety cap, if any
            if max_count is not None and count >= max_count:
                break
        # Final progress update
        progress_indicator(total, total)

    # If actual count differs from estimate, show final actual count
    if count != est_count:
        sys.stderr.write(f"Actual count: {count:,} (vs estimated: ~{est_count:,})\n")
        sys.stderr.flush()

    # --- Step 6: Print summary ---
    print_summary(count, output_path)

    # --- Step 7: Optionally save settings as YAML profile ---
    if sys.stdin.isatty():
        try:
            save_choice = input("\nSave current settings as a YAML profile? (y/n): ").strip().lower()
            if save_choice in ("y", "yes"):
                profile_path = input("Enter profile filename (e.g. my-profile.yaml): ").strip()
                if profile_path:
                    save_profile(config, profile_path)
                    print(f"Profile saved to {profile_path}")
                else:
                    print("No filename provided. Skipping profile save.")
        except (EOFError, KeyboardInterrupt):
            print()


if __name__ == "__main__":
    main()
