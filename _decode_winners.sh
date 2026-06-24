for w in \
  DU25XyVifBP3bTetkK124cZ2iXcxvXw7dpZrgzKoVJvW \
  C3zPgDxqJYN4Gf9RotKuHTbBMfpKEsAFSY7hSbFWdSdQ \
  B1zhrWqDoJdZ9paihqcLN3UY1LzXxxch1zLBzgoaQnKz \
  Zsp75pPCt115MgZZwAgmKyDq8vZ94cjBNnLPhwJespQ \
  jStURX8S5RX8iVvV9ajQ2nkSdzakc4hFpn7RFY6fMYz \
  7d54PtGUtqVPZoF3qzGHRFUQQstrHyCdm4uwwrhDSHLW \
  ArWirdsAEcB9etH4RoAkp4Khx8PiXryEm44A8RBqv7Kr \
  DaxfeJKeVyinftwWSV47p8EgiExYBDquRRUCxki3FrEz \
  DznHqBUWQR1uVJxKkY1cgZuu2C1qzisU7qGSutGyte1a ; do
  echo "########## $w ##########"
  python scripts/wallet_decode.py "$w" 150 2>&1
  echo ""
done
