# Decode the undecoded drawdown winners + the fast-hold re-examine, flag time-box signature.
for w in \
  7d54PtGUtqVPZoF3qzGHRFUQQstrHyCdm4uwwrhDSHLW \
  9LUH6U5x33wmYnZs4JjjJuUwYbeLRNTYxmmNScVR5fBL \
  KGqhAC8HJrSbRfTxLC3dBKr8S6jD5otHWXcqkN8LF9z \
  3GDfzrjrV3cvZE1g71mvV6ipfNTR1qrHQsC3tDWLr81i \
  8G2mgkgEjs59CoKzh4Veeia2V2HiBZfwEWKzyPzuxtFA \
  Gj8DM8DLBXvQ6x1DdALbCHWSrtybwtNQtVJ7XuPPCgU5 \
  6SckxkNGVq8345g14FJLvDiQz6cMnQfJPzMB4sKLYPjq \
  DzuQjKaRLsuQgrxbUJKq3wzoeVYfg3Cn9QcJQzq8vyzX \
  7c82ZFFQThHprwYNsB9Lc1zz2EpCD2uiQ8g4iK1uiHo3 \
  G1cnfANoMiT8D25jcascmUwyWXJuaW4o5DFny25GFZ6w \
  4mAW6RMGQjCKddRQ6ZZnSU59RA7WJJxS9XZKVVuVeEGy ; do
  echo "########## $w ##########"
  python scripts/wallet_decode.py "$w" 150 2>&1 | grep -E "DECODE|SIZING|HOLDS|TIME-BOX|RETURNS|loser exits" 
  echo ""
done
