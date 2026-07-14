import scratch_steepfall as s
wallets=[
 ("C3zPgDxqJYN4Gf9RotKuHTbBMfpKEsAFSY7hSbFWdSdQ","C3zP"),
 ("Zsp75pPCt115MgZZwAgmKyDq8vZ94cjBNnLPhwJespQ","Zsp75"),
 ("B1zhrWqDoJdZ9paihqcLN3UY1LzXxxch1zLBzgoaQnKz","B1zhrW"),
 ("jStURX8S5RX8iVvV9ajQ2nkSdzakc4hFpn7RFY6fMYz","jStURX"),
 ("DznHqBUWQR1uVJxKkY1cgZuu2C1qzisU7qGSutGyte1a","DznHqB"),
]
for a,l in wallets:
    try:
        s.run(a,l,150)
    except Exception as e:
        print(f"##### {l} FAILED {e}", flush=True)
