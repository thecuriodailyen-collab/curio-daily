# Curio Daily

One surprising fact every day, published automatically on YouTube Shorts (@CurioDailyEn) and Facebook Reels.

- facts.json: hand-written facts (published first, in order)
- render.py: builds the 1080x1920 video (voice, captions, original music)
- publish.py: uploads to YouTube and Facebook
- generate.py: asks Claude for a new fact once the bank is used up
- .github/workflows/daily.yml: runs every day at 17:00 UTC

Secrets needed (Settings > Secrets and variables > Actions): YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, FB_PAGE_ID, FB_PAGE_TOKEN, ANTHROPIC_API_KEY.

Website and privacy policy: index.html (GitHub Pages).
