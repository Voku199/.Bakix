# Quick sanity check of _parse_ai_response salvage + _strip_degenerate_tail.
import os
os.environ.setdefault("GEMINI_API_KEY", "test")

from app.services.gemini_service import _parse_ai_response, _strip_degenerate_tail

# 1. Degenerate output from the user's bug report: broken JSON + "</" spam
garbage = ('{ "message": "Chceš se zlepšit v konkrétním předmětu? '
           'Dej mi více informací' + '</' * 150)
out = _parse_ai_response(garbage)
assert out["intent"] == "chat"
assert out["message"].startswith("Chceš se zlepšit"), out["message"]
assert "</" not in out["message"], out["message"][-50:]
assert not out["message"].lstrip().startswith("{"), out["message"][:50]
print("salvaged:", repr(out["message"][-60:]))

# 2. Valid JSON must still parse normally
ok = _parse_ai_response('{"message": "normální odpověď", "intent": "chat"}')
assert ok["message"] == "normální odpověď"

# 3. Plain text passes through, repetition tail gets cut
plain = _parse_ai_response("Ahoj! Jak ti můžu pomoct se známkami?")
assert plain["message"] == "Ahoj! Jak ti můžu pomoct se známkami?"
spam = _parse_ai_response("Odpověď." + " ano" * 40)
assert "ano ano" not in spam["message"], spam["message"]

# 4. Normal endings survive untouched
assert _strip_degenerate_tail("Hotovo!") == "Hotovo!"
assert _strip_degenerate_tail("Skvělé!!!") == "Skvělé!!!"

print("OK")
