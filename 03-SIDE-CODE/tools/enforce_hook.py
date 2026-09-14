import argparse
from pathlib import Path
import re


HOOK_KEYWORDS = [
    'betray', 'betrayal', 'choice', 'choices', 'final', 'hidden', 'secret', 'lost', 'why', 'shocking',
    'myster', 'surprising', 'truth', 'real', 'forever', 'legend'
]


def first_nonempty_line(text: str):
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    return ''


def is_hook_ok(line: str) -> bool:
    # baseline: 2-5 words and contains a keyword or punctuation
    words = re.findall(r"\w+'?\w*", line)
    if not (2 <= len(words) <= 5):
        return False
    if '?' in line or '!' in line:
        return True
    lw = line.lower()
    for k in HOOK_KEYWORDS:
        if k in lw:
            return True
    return False


def is_hook_strong(line: str) -> bool:
    # stricter: 2-4 words, contains an emotional/strong keyword, and not generic
    words = re.findall(r"\w+'?\w*", line)
    if not (2 <= len(words) <= 4):
        return False
    lw = line.lower()
    # require punctuation or one of the highest-priority keywords
    strong_keywords = ['betray', 'betrayal', 'final', 'lost', 'shocking', 'truth', 'hidden', 'secret']
    if '?' in line or '!' in line:
        return True
    for k in strong_keywords:
        if k in lw:
            return True
    return False


def generate_hook_from_text(text: str) -> str:
    # Create a single high-quality hook from the strongest keyword
    lw = text.lower()
    mapping = {
        'betray': 'His final choice',
        'betrayal': 'Betrayal revealed',
        'choice': 'His final choice',
        'choices': 'One impossible choice',
        'final': 'His final choice',
        'hidden': 'The hidden reason',
        'secret': 'The hidden secret',
        'lost': 'Lost forever',
        'why': 'The real reason',
        'truth': 'Nobody knew the truth',
        'surprising': 'Nobody believed him',
        'shocking': 'Shocking moment',
        'myster': 'The hidden mystery',
        'legend': 'The hidden legend',
    }
    for key in mapping:
        if key in lw:
            return mapping[key]
    return 'The real reason'


def generate_hook_variants(text: str, n: int = 4):
    """Return up to `n` hook candidates with simple diversity and emotion scoring.

    Each candidate is a dict: {"hook": str, "score": float, "emotion": str}
    """
    candidates = []
    lw = text.lower()

    # base mapping for short hooks grouped by emotion
    groups = {
        'dramatic': ['His final choice', 'One impossible choice', 'Lost forever'],
        'mystery': ['The hidden reason', 'The hidden secret', 'Nobody knew the truth'],
        'shocking': ['Betrayal revealed', 'Shocking moment', 'Nobody believed him'],
        'curiosity': ['The real reason', 'The hidden legend', 'The hidden mystery'],
        'question': ['Why did he do it?', 'Who betrayed whom?', 'What changed everything?']
    }

    # emotion scoring: simple lexicon counts
    lex = {
        'dramatic': ['choice', 'choices', 'final', 'upended', 'consequences', 'risk'],
        'mystery': ['hidden', 'secret', 'why', 'myster', 'unknown'],
        'shocking': ['betray', 'betrayal', 'shocking', 'surprising'],
        'curiosity': ['truth', 'real', 'legend', 'surprising'],
        'question': ['why', 'what', 'who', '?']
    }

    def score_emotion(em):
        s = 0
        for kw in lex.get(em, []):
            if kw in lw:
                s += 1
        return s

    # produce candidate list by selecting relevant groups first
    ranked = []
    for em in groups:
        sc = score_emotion(em)
        ranked.append((sc, em))
    ranked.sort(reverse=True)

    # flatten phrases, prefer higher-ranked emotion groups
    for sc, em in ranked:
        for phrase in groups[em]:
            if len(candidates) >= n:
                break
            # score combines emotion presence and phrase length penalty (shorter preferred)
            score = sc + (1.0 / (1 + len(phrase.split())))
            candidates.append({'hook': phrase, 'score': float(score), 'emotion': em})
        if len(candidates) >= n:
            break

    # ensure uniqueness and limit to n
    seen = set()
    out = []
    for c in candidates:
        h = c['hook']
        if h in seen:
            continue
        seen.add(h)
        out.append(c)
        if len(out) >= n:
            break
    return out


def ensure_hook_first(script_text: str, hook: str) -> str:
    lines = [line.rstrip() for line in script_text.splitlines() if line.strip()]
    if not lines:
        return hook
    lines[0] = hook
    return "\n".join(lines)


def normalize_hook_length(hook: str, fallback: str = 'The real reason') -> str:
    words = re.findall(r"\w+'?\w*", hook or '')
    if len(words) < 2:
        return fallback
    return ' '.join(words[:5])


def enforce_hook(script_path: Path, enforce: bool = True, strict: bool = False, replace_if_weak: bool = False) -> int:
    if not script_path.exists():
        print(f'No script at {script_path}')
        return 2
    txt = script_path.read_text(encoding='utf-8')
    first = first_nonempty_line(txt)
    if strict:
        ok = is_hook_strong(first)
    else:
        ok = is_hook_ok(first)
    if ok:
        print('OK: script already starts with a valid hook:', first)
        return 0
    if not enforce:
        print('INVALID: script does not start with a valid hook')
        print('First line:', first)
        return 1
    # generate hook and prepend
    hook = normalize_hook_length(generate_hook_from_text(txt))
    # if replace_if_weak and there's an existing first line, replace it
    if replace_if_weak and first:
        remaining = '\n'.join([l for l in txt.splitlines()[1:]])
        new_txt = hook + '\n' + remaining.lstrip()
        script_path.write_text(new_txt, encoding='utf-8')
        print('REPLACED weak hook with ->', hook)
        return 0
    new_txt = hook + '\n' + txt.lstrip()  # ensure hook is first line
    script_path.write_text(new_txt, encoding='utf-8')
    print('ENFORCED: prepended hook ->', hook)
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('script', help='path to script.txt')
    p.add_argument('--no-enforce', action='store_true', help='only validate, do not modify')
    p.add_argument('--strict', action='store_true', help='require a stronger hook')
    p.add_argument('--replace', action='store_true', help='replace existing weak first line with generated hook')
    args = p.parse_args()
    script_path = Path(args.script)
    return enforce_hook(script_path, enforce=not args.no_enforce, strict=args.strict, replace_if_weak=args.replace)


if __name__ == '__main__':
    raise SystemExit(main())
