from flask import json

try:
    from langfuse import observe as _lf_observe, get_client as _lf_get_client, propagate_attributes as _lf_propagate
    _LF_AVAILABLE = True
except ImportError:
    _LF_AVAILABLE = False
    def _lf_observe(*a, **kw):
        if a and callable(a[0]):
            return a[0]
        return lambda fn: fn
    def _lf_get_client():
        return None
    class _NoopPropagateAttrs:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def _lf_propagate(**kw):
        return _NoopPropagateAttrs()

from prompts.constants import PLACE_COLS_PROMPT, HOTEL_COLS_PROMPT, REST_COLS_PROMPT


def trim_for_prompt(system, df, cols, n):
    available = [c for c in cols if c in df.columns]
    return df[available].head(n)


_TEXT_COLS = ('editorial_summary', 'review_summary', 'famous activities', 'best month to visit')

def truncate_text_cols(df, max_chars=120):
    df = df.copy()
    for col in _TEXT_COLS:
        if col in df.columns:
            df[col] = df[col].astype(str).str.slice(0, max_chars)
    return df


def places_count_for_trip(trip_duration):
    d = int(trip_duration or 3)
    if d >= 9:
        return 50
    if d >= 7:
        return 40
    return 30


def accumulate_usage(itinerary, response):
    if not isinstance(itinerary, dict):
        return
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    acc = itinerary.setdefault("_token_usage", {"input_token": 0, "output_token": 0})
    acc["input_token"] += int(getattr(usage, "input_tokens", 0) or 0)
    acc["output_token"] += int(getattr(usage, "output_tokens", 0) or 0)


def log_json_decode_error(response, response_text, err):
    pos = getattr(err, "pos", None)
    status = getattr(response, "status", None)
    usage = getattr(response, "usage", None)
    print("=" * 70)
    print(f"[itinerary JSON parse FAILED] {err}")
    print(f"  response.status = {status}")
    print(f"  incomplete_details = {getattr(response, 'incomplete_details', None)}")
    print(f"  usage = {usage}")
    print(f"  output text length = {len(response_text)} chars")
    if pos is not None:
        lo, hi = max(0, pos - 200), min(len(response_text), pos + 200)
        print(f"  --- text around char {pos} (showing {lo}:{hi}) ---")
        print(repr(response_text[lo:hi]))
    print(f"  --- last 200 chars of output ---")
    print(repr(response_text[-200:]))
    print("=" * 70)


def extract_completed_json(response):
    status = getattr(response, "status", None)
    if status == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "max_output_tokens":
            raise ValueError(
                "Itinerary generation exceeded the model output limit "
                "(response truncated). Try a shorter trip_duration or raise "
                "max_tokens / the model's max output tokens."
            )
        raise ValueError(f"Itinerary generation did not complete (status=incomplete, reason={reason}).")
    return response.output_text


def find_json_objects(text):
    """Yield every balanced top-level {...} substring in `text`."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = False
        escaped = False
        start = i
        j = i
        closed = False
        while j < n:
            ch = text[j]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        yield text[start:j + 1]
                        closed = True
                        break
            j += 1
        i = (j + 1) if closed else (start + 1)


def sanitize_llm_json(text):
    out = []
    in_string = False
    escaped = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_itinerary_json(response_text):
    candidates = list(find_json_objects(response_text))
    if not candidates:
        candidates = [response_text]

    best = None
    best_days = -1
    last_err = None
    for raw in candidates:
        try:
            cleaned = sanitize_llm_json(raw.strip())
            parsed = json.loads(cleaned, strict=False)
        except ValueError as e:
            last_err = e
            continue
        if not isinstance(parsed, dict) or "itinerary" not in parsed:
            continue
        days = len(parsed.get("itinerary") or [])
        if days > best_days:
            best, best_days = parsed, days

    if best is not None:
        return best
    if last_err is not None:
        raise last_err
    raise ValueError("No itinerary JSON object found in model response.")


def days_from_obj(obj):
    if not isinstance(obj, dict):
        return None
    for key in ("itinerary", "days"):
        if isinstance(obj.get(key), list):
            return obj[key]
    if "timeline" in obj or "places_to_visit" in obj or "day" in obj:
        return [obj]
    return None


def parse_days_json(response_text):
    for raw in find_json_objects(response_text):
        try:
            obj = json.loads(sanitize_llm_json(raw.strip()), strict=False)
        except ValueError:
            continue
        days = days_from_obj(obj)
        if days:
            return days
    cleaned = sanitize_llm_json(response_text.strip())
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    parsed = json.loads(cleaned, strict=False)
    if isinstance(parsed, list):
        return parsed
    days = days_from_obj(parsed)
    if days:
        return days
    raise ValueError("Follow-up response did not contain a days array.")


def collect_used_place_names(days):
    names = []
    for day in days:
        if not isinstance(day, dict):
            continue
        for item in day.get('timeline', []) or []:
            if not isinstance(item, dict) or item.get('type') != 'place':
                continue
            name = str(item.get('name', '')).strip()
            if name and name not in names:
                names.append(name)
    return names


@_lf_observe(name="trip_skeleton", as_type="generation")
def generate_trip_skeleton(system, user_preferences, top_places, top_restaurants, top_hotels):
    from prompts import generate_trip_skeleton_prompt
    messages = generate_trip_skeleton_prompt(
        user_preferences, top_places, top_restaurants, top_hotels
    )
    response = system.client.responses.create(
        model=system.chat_deployment,
        input=messages,
        max_output_tokens=system.max_tokens,
        text={"format": {"type": "json_object"}},
    )
    _lf_get_client().update_current_generation(
        model=system.chat_deployment,
        usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
        output=response.output_text,
    )
    response_text = extract_completed_json(response)
    skeleton = None
    for raw in find_json_objects(response_text):
        try:
            obj = json.loads(sanitize_llm_json(raw.strip()), strict=False)
        except ValueError:
            continue
        if isinstance(obj, dict):
            skeleton = obj
            break
    if skeleton is None:
        try:
            skeleton = json.loads(sanitize_llm_json(response_text.strip()), strict=False)
        except ValueError as e:
            log_json_decode_error(response, response_text, e)
            raise
    if not isinstance(skeleton, dict):
        skeleton = {}
    skeleton.setdefault('itinerary', [])
    skeleton.setdefault('hotels', [])
    skeleton.setdefault('similar_places', [])
    accumulate_usage(skeleton, response)
    return skeleton


@_lf_observe(name="detailed_itinerary", as_type="generation")
def generate_detailed_itinerary(system, user_preferences, top_places, top_hotels, top_restaurants, rest_slot_counts=None):
    from prompts import generate_travel_itinerary_prompt
    n_places = places_count_for_trip(user_preferences.get('trip_duration', 3))
    places_trimmed = truncate_text_cols(trim_for_prompt(system, top_places, PLACE_COLS_PROMPT, n_places))
    hotels_trimmed = trim_for_prompt(system, top_hotels, HOTEL_COLS_PROMPT, 10)
    rests_trimmed  = trim_for_prompt(system, top_restaurants, REST_COLS_PROMPT, 20)
    messages = generate_travel_itinerary_prompt(
        user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
        rest_slot_counts=rest_slot_counts,
    )
    print(f"Prompt length: {sum(len(m['content']) for m in messages)} chars")

    response = system.client.responses.create(
        model=system.chat_deployment,
        input=messages,
        max_output_tokens=system.max_tokens,
        text={"format": {"type": "json_object"}},
    )
    _lf_get_client().update_current_generation(
        model=system.chat_deployment,
        usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
        output=response.output_text,
    )
    response_text = extract_completed_json(response)
    try:
        itinerary = parse_itinerary_json(response_text)
    except ValueError as e:
        log_json_decode_error(response, response_text, e)
        raise

    accumulate_usage(itinerary, response)
    return ensure_full_days(
        system, itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
        rest_slot_counts=rest_slot_counts,
    )


def ensure_full_days(system, itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed, rest_slot_counts=None):
    try:
        target = int(user_preferences.get('trip_duration'))
    except (TypeError, ValueError):
        return itinerary
    if not isinstance(itinerary, dict):
        return itinerary

    days = itinerary.get('itinerary')
    if not isinstance(days, list):
        return itinerary

    attempts = 0
    while len(days) < target and attempts < target:
        attempts += 1
        used_places = collect_used_place_names(days)
        missing = target - len(days)
        extra = generate_extra_days(
            system, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
            start_day=len(days) + 1, num_days=missing, used_places=used_places,
            itinerary=itinerary, rest_slot_counts=rest_slot_counts,
        )
        if not extra:
            print(f"[days] top-up call returned no new days (have {len(days)}/{target}); stopping.")
            break
        days.extend(extra)

    days = days[:target] if len(days) > target else days
    for idx, day in enumerate(days, start=1):
        if isinstance(day, dict):
            day['day'] = idx
    itinerary['itinerary'] = days
    if len(days) < target:
        print(f"[days] WARNING: could only build {len(days)}/{target} days after top-up.")
    return itinerary


@_lf_observe(name="extra_days", as_type="generation")
def generate_extra_days(system, user_preferences, top_places, top_restaurants, top_hotels,
                        start_day, num_days, used_places, itinerary=None, rest_slot_counts=None):
    from prompts import generate_extra_days_prompt
    messages = generate_extra_days_prompt(
        user_preferences, top_places, top_restaurants, top_hotels,
        start_day=start_day, num_days=num_days, used_places=used_places,
        rest_slot_counts=rest_slot_counts,
    )
    print(f"[days] requesting {num_days} extra day(s) starting at day {start_day}")
    _lf_get_client().update_current_span(
        metadata={"start_day": start_day, "num_days": num_days},
    )
    response = system.client.responses.create(
        model=system.chat_deployment,
        input=messages,
        max_output_tokens=system.max_tokens,
        text={"format": {"type": "json_object"}},
    )
    _lf_get_client().update_current_generation(
        model=system.chat_deployment,
        usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
        output=response.output_text,
    )
    if itinerary is not None:
        accumulate_usage(itinerary, response)
    response_text = extract_completed_json(response)
    try:
        return parse_days_json(response_text)
    except ValueError as e:
        log_json_decode_error(response, response_text, e)
        return []


@_lf_observe(name="destination_description", as_type="generation")
def generate_destination_description(system, destination):
    destination = str(destination or "").strip()
    if not destination:
        return ""
    try:
        prompt = (
            f"Write a short, engaging 1-2 sentence travel description for "
            f"{destination}. Only return the description text — no titles, "
            f"quotes, markdown, or extra commentary."
        )
        response = system.client.responses.create(
            model=system.chat_deployment,
            input=[{"role": "user", "content": prompt}],
        )
        result = (response.output_text or "").strip()
        _lf_get_client().update_current_generation(
            model=system.chat_deployment,
            input=prompt,
            output=result,
            usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens} if response.usage else None,
        )
        return result
    except Exception as e:
        print(f"  _generate_destination_description failed for {destination!r}: {e}")
        return ""
