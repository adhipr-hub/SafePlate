from safeplate.brave_search import brave_web_search
from safeplate.config import (
    get_user_agent, get_brave_search_api_key, get_gemini_api_key, get_gemini_model,
)
import safeplate.community_signals as cs

ua = get_user_agent()
bk = get_brave_search_api_key()
print("brave key present:", bool(bk), "| gemini key:", bool(get_gemini_api_key()))

q = '"Golden Diner" "New York" allergy OR allergic OR nut'
try:
    rs = brave_web_search(query=q, api_key=bk, user_agent=ua, count=6)
    print("brave results:", len(rs))
    for r in rs[:4]:
        print("   -", (r.title or "")[:55], "|", (r.description or "")[:90])
except Exception as e:
    print("BRAVE ERROR:", repr(e))

print("\n_search (no swallow):")
try:
    snip, urls = cs._search(restaurant_name="Golden Diner",
                            address="123 Madison St, New York, NY 10002",
                            api_key=bk, user_agent=ua, want_dishes=True)
    print("  snippet len:", len(snip), "| urls:", len(urls))
    print("  sample:", snip[:260])
    if snip.strip():
        parsed = cs._classify(snip, api_key=get_gemini_api_key(), model=get_gemini_model())
        print("  LLM parsed handling:", len(parsed.get("handling", [])),
              "dishes:", parsed.get("dishes", [])[:8])
except Exception as e:
    print("  ERROR:", repr(e))
