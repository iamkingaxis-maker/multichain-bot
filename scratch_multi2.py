import scratch_steepfall as s
for a,l in [
 ("B1zhrWqDoJdZ9paihqcLN3UY1LzXxxch1zLBzgoaQnKz","B1zhrW"),
 ("jStURX8S5RX8iVvV9ajQ2nkSdzakc4hFpn7RFY6fMYz","jStURX"),
]:
    try: s.run(a,l,120)
    except Exception as e: print(f"##### {l} FAILED {e}", flush=True)
