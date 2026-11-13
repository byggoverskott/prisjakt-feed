# prisjakt-feed

Produktflöde för Prisjakt från byggoverskott.se (Shopify). Ersätter LitCommerce, som har en gräns på 1 000 produkter.

**Flödets adress (läggs in i Prisjakt Business Center):**
https://byggoverskott.github.io/prisjakt-feed/prisjakt.xml

**Senaste körningen** (antal produkter, hur många som har EAN, vad som valts bort):
https://byggoverskott.github.io/prisjakt-feed/status.json

## Så fungerar det
GitHub Actions kör `feed.py` en gång per dygn (ca 05:23 svensk tid). Den kan också köras manuellt under fliken Actions. Skriptet hämtar alla aktiva produkter från Shopify och publicerar XML-filen via GitHub Pages. Om flödet skulle bli tomt publiceras det inte, och den förra filen ligger kvar.

## Regler
- Med: aktiva produkter som är publicerade i webbutiken, i lager och har minst en bild.
- Ej med: produkter taggade `endast-butik`, eftersom de bara kan hämtas i butiken.
- Ej med: produkter som kostar under 250 kr (det faktiska priset, inkl. moms). Prisjakt tar betalt per klick, och billiga produkter ger för lite vinst för att bära klickkostnaden. Gränsen ändras i `MIN_PRICE` i `feed.py`.
- EAN (`gtin`) hämtas från streckkoden i Shopify. Ogiltiga koder skickas inte med.
- Pris: jämförpriset skickas som `price` och det faktiska priset som `sale_price`.
- Taggen `återbruk` ger skick `used`, annars `new`.
- Märke = leverantör i Shopify. Olika stavningar av samma märke (ESSVE/Essve) slås ihop.
- Vikt skickas i kg.
- ID: produkter med en variant behåller Shopifys produkt-ID (samma som LitCommerce använde), så att Prisjakts matchningar ligger kvar. Produkter med flera varianter får en rad per variant.

## Nycklar
Ligger som krypterade Secrets under Settings > Secrets and variables > Actions:
`SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` (samma Shopify-app som i tradera-sync).
