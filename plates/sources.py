"""Turn a tile's sources into a ranked list of titles with artwork.

Supported source types:
  tmdbDiscover, tmdbList, tmdbCollection, traktList (needs TRAKT_CLIENT_ID)
"""
import datetime as dt
import io
import os
import time
from collections import OrderedDict

import requests
from PIL import Image

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p"
TRAKT_API = "https://api.trakt.tv"


class TMDB:
    def __init__(self, key):
        self.s = requests.Session()
        self.params = {}
        if key.startswith("eyJ"):            # v4 read access token
            self.s.headers["Authorization"] = f"Bearer {key}"
        else:                                # v3 api key
            self.params["api_key"] = key
        self._img_cache = OrderedDict()   # small LRU: full-size stills are big
        self._img_cache_max = 30

    def get(self, path, **params):
        for attempt in range(4):
            r = self.s.get(f"{TMDB_API}{path}", params={**self.params, **params}, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            self._check(r, path)
            return r.json()
        self._check(r, path)

    @staticmethod
    def _check(r, path):
        """Raise without the request URL, so the API key never lands in a public log."""
        if not r.ok:
            raise requests.HTTPError(f"TMDB {r.status_code} on {path}")

    def image(self, path, size="w1280"):
        key = (path, size)
        if key in self._img_cache:
            self._img_cache.move_to_end(key)
            return self._img_cache[key]
        r = self.s.get(f"{TMDB_IMG}/{size}{path}", timeout=60)
        self._check(r, f"image {path}")
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        if max(img.size) > 2400:           # 'original' can be 4K+; we never need more than 1920 wide
            img.thumbnail((2400, 2400), Image.LANCZOS)
        self._img_cache[key] = img
        while len(self._img_cache) > self._img_cache_max:
            self._img_cache.popitem(last=False)
        return img

    def clean_backdrop(self, media, tmdb_id, fallback):
        """Prefer a backdrop with no text or logo on it."""
        try:
            data = self.get(f"/{media}/{tmdb_id}/images", include_image_language="null")
            shots = [b for b in data.get("backdrops", []) if b.get("iso_639_1") is None]
            shots.sort(key=lambda b: (b.get("vote_average", 0), b.get("width", 0)), reverse=True)
            if shots:
                return shots[0]["file_path"]
        except requests.HTTPError:
            pass
        return fallback


    def clean_poster(self, media, tmdb_id):
        """A poster with no text on it, if TMDB has one. Many titles don't."""
        try:
            data = self.get(f"/{media}/{tmdb_id}/images", include_image_language="null")
            shots = [p for p in data.get("posters", []) if p.get("iso_639_1") is None]
            shots.sort(key=lambda p: (p.get("vote_average", 0), p.get("width", 0)), reverse=True)
            if shots:
                return shots[0]["file_path"]
        except requests.HTTPError:
            pass
        return None


    # ---------- logos ----------

    def best_logo(self, kind, oid):
        """Best PNG logo TMDB has for a network or company (kind: 'network' or 'company')."""
        try:
            logos = self.get(f"/{kind}/{oid}/images").get("logos", [])
        except requests.HTTPError:
            return None
        pngs = [l for l in logos if str(l.get("file_type", ".png")).lower() == ".png" or l["file_path"].endswith(".png")]
        pngs.sort(key=lambda l: (l.get("vote_average", 0), l.get("width", 0)), reverse=True)
        return pngs[0]["file_path"] if pngs else None

    def all_logos(self, kind, oid, limit=6):
        """Every PNG logo TMDB has for a network or company, best first."""
        try:
            logos = self.get(f"/{kind}/{oid}/images").get("logos", [])
        except requests.HTTPError:
            return []
        pngs = [l for l in logos if l["file_path"].endswith(".png")]
        pngs.sort(key=lambda l: (l.get("vote_average", 0), l.get("width", 0)), reverse=True)
        return [l["file_path"] for l in pngs[:limit]]

    def logo_image(self, path):
        r = self.s.get(f"{TMDB_IMG}/original{path}", timeout=60)
        self._check(r, f"logo {path}")
        return Image.open(io.BytesIO(r.content)).convert("RGBA")

    def person_headshot(self, name):
        """Best profile photo TMDB has for a person, looked up by name."""
        pid = resolve_person(self, name)
        if not pid:
            return None
        try:
            profiles = self.get(f"/person/{pid}/images").get("profiles", [])
        except requests.HTTPError:
            return None
        profiles.sort(key=lambda p: (p.get("vote_average", 0), p.get("height", 0)), reverse=True)
        if not profiles:
            return None
        return self.image(profiles[0]["file_path"], "original")

    def search_company(self, name):
        res = self.get("/search/company", query=name).get("results", [])
        exact = [r for r in res if r["name"].lower() == name.lower()]
        hit = (exact or res or [None])[0]
        return hit["id"] if hit else None

    def provider_icon(self, provider_name):
        """The service's own app icon from TMDB (e.g. Netflix's red N on black)."""
        r = find_provider(self, provider_name)
        if not r or not r.get("logo_path"):
            return None, None
        return self.logo_image(r["logo_path"]), r["provider_name"]

    def service_network(self, provider_name, service_name):
        """Find the TMDB network behind a streaming service (e.g. Netflix -> network 213)
        by looking at the networks of its most popular shows."""
        pid = resolve_provider(self, provider_name)
        if not pid:
            return None
        shows = self.get("/discover/tv", with_watch_providers=pid, watch_region="US",
                         sort_by="popularity.desc", **{"vote_count.gte": 100}).get("results", [])[:12]
        counts, names = {}, {}
        for sh in shows:
            try:
                for n in self.get(f"/tv/{sh['id']}").get("networks", []):
                    counts[n["id"]] = counts.get(n["id"], 0) + 1
                    names[n["id"]] = n["name"]
            except requests.HTTPError:
                continue
        if not counts:
            return None
        key = service_name.lower().replace("+", "").replace(" ", "")
        for nid, nm in names.items():
            if key in nm.lower().replace("+", "").replace(" ", ""):
                return nid
        return max(counts, key=counts.get)


class Wikimedia:
    """Fetch an award logo from Wikimedia Commons by searching file names."""
    API = "https://commons.wikimedia.org/w/api.php"

    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "q7m-plates/1.0 (personal image build)"

    def search_logo(self, query, width=1200):
        params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                  "gsrsearch": query, "gsrlimit": 10, "prop": "imageinfo",
                  "iiprop": "url|mime", "iiurlwidth": width}
        pages = self.s.get(self.API, params=params, timeout=30).json().get("query", {}).get("pages", {})
        for pg in sorted(pages.values(), key=lambda p: p.get("index", 99)):
            info = (pg.get("imageinfo") or [{}])[0]
            if info.get("mime") in ("image/svg+xml", "image/png") and info.get("thumburl"):
                r = self.s.get(info["thumburl"], timeout=60)
                if r.ok:
                    return Image.open(io.BytesIO(r.content)).convert("RGBA"), pg.get("title")
        return None, None

    def file(self, title, width=1200):
        """Fetch a specific Commons file, e.g. 'File:Academy Awards logo.svg'."""
        params = {"action": "query", "format": "json", "titles": title, "prop": "imageinfo",
                  "iiprop": "url|mime", "iiurlwidth": width}
        pages = self.s.get(self.API, params=params, timeout=30).json().get("query", {}).get("pages", {})
        for pg in pages.values():
            info = (pg.get("imageinfo") or [{}])[0]
            if info.get("thumburl"):
                r = self.s.get(info["thumburl"], timeout=60)
                if r.ok:
                    return Image.open(io.BytesIO(r.content)).convert("RGBA"), title
        return None, None


class Trakt:
    def __init__(self, client_id):
        self.s = requests.Session()
        self.s.headers.update({
            "trakt-api-version": "2",
            "trakt-api-key": client_id,
            "Content-Type": "application/json",
        })

    def list_items(self, username, slug):
        r = self.s.get(f"{TRAKT_API}/users/{username}/lists/{slug}/items", timeout=30)
        r.raise_for_status()
        out = []
        for it in r.json():
            kind = it.get("type")
            obj = it.get(kind) or {}
            tid = (obj.get("ids") or {}).get("tmdb")
            if tid and kind in ("movie", "show"):
                out.append(("movie" if kind == "movie" else "tv", tid))
        return out


HORROR = 27


def _discover_params(q, min_votes, mode=None, no_horror_kw=None):
    sort = "vote_count.desc" if mode == "classic" else "popularity.desc"
    p = {"sort_by": sort, "include_adult": "false", "page": 1}
    if q.get("genres"):
        p["with_genres"] = ",".join(map(str, q["genres"]))
    without = list(q.get("excludeGenres") or [])
    tv = q.get("mediaType") == "tv"
    if not tv and HORROR not in without:
        without.append(HORROR)
    if without:
        p["without_genres"] = ",".join(map(str, without))
    if tv and no_horror_kw:
        p["without_keywords"] = str(no_horror_kw)
    if q.get("keywords"):
        p["with_keywords"] = "|".join(map(str, q["keywords"]))
    if q.get("companies"):
        p["with_companies"] = "|".join(map(str, q["companies"]))
    if q.get("networks"):
        p["with_networks"] = "|".join(map(str, q["networks"]))
    if q.get("originalLanguage"):
        p["with_original_language"] = q["originalLanguage"]
    if mode == "fresh" and not q.get("releaseDateGte") and not q.get("releaseDateLte"):
        since = (dt.date.today() - dt.timedelta(days=540)).isoformat()
        p["first_air_date.gte" if tv else "primary_release_date.gte"] = since
    if q.get("releaseDateGte"):
        p["first_air_date.gte" if tv else "primary_release_date.gte"] = q["releaseDateGte"]
    if q.get("releaseDateLte"):
        p["first_air_date.lte" if tv else "primary_release_date.lte"] = q["releaseDateLte"]
    if q.get("providers"):
        p["with_watch_providers"] = "|".join(map(str, q["providers"]))
        p["watch_region"] = q.get("region", "US")
    if q.get("crew"):
        p["with_crew"] = "|".join(map(str, q["crew"]))
    if q.get("voteAverageGte"):
        p["vote_average.gte"] = q["voteAverageGte"]
    p["vote_count.gte"] = max(int(q.get("voteCountGte") or 0), min_votes)
    return p


def _is_horror(media, r):
    return media == "movie" and HORROR in (r.get("genre_ids") or [g.get("id") for g in r.get("genres", [])])


def _norm(media, r):
    return {
        "media": media,
        "id": r["id"],
        "title": r.get("title") or r.get("name") or "",
        "backdrop": r.get("backdrop_path"),
        "popularity": r.get("popularity", 0),
    }


_kw_cache, _person_cache = {}, {}


def resolve_keyword(tmdb, text):
    """Find a TMDB keyword id by name. Exact match wins, otherwise the first hit."""
    if text not in _kw_cache:
        res = tmdb.get("/search/keyword", query=text).get("results", [])
        exact = [r for r in res if r["name"].lower() == text.lower()]
        _kw_cache[text] = (exact or res or [{"id": None}])[0]["id"]
    return _kw_cache[text]


_provider_cache = {}
_SKIP = ("amazonchannel", "rokupremiumchannel", "applechannel", "channel", "withads", "basic")


def _norm_name(n):
    n = n.lower().replace("+", "plus")
    return "".join(ch for ch in n if ch.isalnum())


def find_provider(tmdb, name, region="US"):
    """Look a streaming service up in TMDB's US provider list by name (or a list of
    alternative names, tried in order). Returns the provider dict."""
    if isinstance(name, (list, tuple)):
        for n in name:
            r = find_provider(tmdb, n, region)
            if r:
                return r
        return None
    key = (name.lower(), region)
    if key in _provider_cache:
        return _provider_cache[key]
    want = _norm_name(name)
    allp = {}
    for media in ("tv", "movie"):
        for r in tmdb.get(f"/watch/providers/{media}", watch_region=region).get("results", []):
            allp[r["provider_id"]] = r
    best = None
    for r in sorted(allp.values(), key=lambda r: r.get("display_priority", 999)):
        n = _norm_name(r["provider_name"])
        if n == want:
            best = r
            break
        if best is None and n.startswith(want) and not any(x in n for x in _SKIP):
            best = r
    _provider_cache[key] = best
    return best


def resolve_provider(tmdb, name, region="US"):
    r = find_provider(tmdb, name, region)
    return r["provider_id"] if r else None


def resolve_person(tmdb, name):
    if name not in _person_cache:
        res = tmdb.get("/search/person", query=name).get("results", [])
        _person_cache[name] = res[0]["id"] if res else None
    return _person_cache[name]


def art_query_source(tmdb, aq):
    """Turn a friendly art query from settings.json into a tmdbDiscover-style source.

    Keys: media ("movie"/"tv"), genres [ids], keywords ["names"], director "name",
    provider "streaming service name" (US watch providers unless region is set),
    before "YYYY-MM-DD", after "YYYY-MM-DD", languages "fr|ko|ja", min_rating, min_votes
    """
    q = {"mediaType": aq.get("media", "movie"), "genres": aq.get("genres", [])}
    if aq.get("keywords"):
        ids = [resolve_keyword(tmdb, k) for k in aq["keywords"]]
        q["keywords"] = [i for i in ids if i]
        if not q["keywords"] and not q["genres"]:
            return None
    if aq.get("director"):
        pid = resolve_person(tmdb, aq["director"])
        if not pid:
            return None
        q["crew"] = [pid]
    if aq.get("provider"):
        pid = resolve_provider(tmdb, aq["provider"], aq.get("region", "US"))
        if not pid:
            return None
        q["providers"] = [pid]
        q["region"] = aq.get("region", "US")
    if aq.get("before"):
        q["releaseDateLte"] = aq["before"]
    if aq.get("after"):
        q["releaseDateGte"] = aq["after"]
    if aq.get("languages"):
        q["originalLanguage"] = aq["languages"]
    if aq.get("min_rating"):
        q["voteAverageGte"] = aq["min_rating"]
    if aq.get("min_votes"):
        q["voteCountGte"] = aq["min_votes"]
    return {"tmdbDiscover": {"_0": q}}


def gather(tile, tmdb, trakt=None, want=14, min_votes=200, art_query=None, mode=None):
    """Return up to `want` distinct titles (most popular first) that have a backdrop.

    Discover queries are re-sorted by popularity with a vote floor so the
    artwork uses recognizable titles even if the row itself sorts by newest.
    """
    seen, out = set(), []
    try:
        no_horror_kw = resolve_keyword(tmdb, "horror")
    except Exception:
        no_horror_kw = None

    def add(media, r):
        key = (media, r["id"])
        if key in seen or not r.get("backdrop_path") or _is_horror(media, r):
            return
        seen.add(key)
        out.append(_norm(media, r))

    sources = list(tile["sources"])
    if art_query:
        extra = art_query_source(tmdb, art_query)
        if extra:
            sources.insert(0, extra)

    for src in sources:
        if len(out) >= want:
            break
        kind = next(iter(src))
        q = src[kind]["_0"]
        try:
            if kind == "tmdbDiscover":
                media = "tv" if q.get("mediaType") == "tv" else "movie"
                res = tmdb.get(f"/discover/{media}", **_discover_params(q, min_votes, mode, no_horror_kw))["results"]
                if mode == "fresh" and len(res) < 8:   # not enough new titles: top up with all-time ones
                    res += tmdb.get(f"/discover/{media}", **_discover_params(q, min_votes, None, no_horror_kw))["results"]
                if len(res) < 6:   # niche query, relax the vote floor
                    res += tmdb.get(f"/discover/{media}", **_discover_params(q, 10, None, no_horror_kw))["results"]
                if len([r for r in res if r.get("backdrop_path")]) < 3:   # very small catalog
                    q0 = {**q, "voteCountGte": 0}
                    p0 = _discover_params(q0, 0, None, no_horror_kw)
                    p0["vote_count.gte"] = 0
                    res = tmdb.get(f"/discover/{media}", **p0)["results"]
                for r in res:
                    add(media, r)
            elif kind == "tmdbList":
                data = tmdb.get(f"/list/{q['listId']}")
                items = sorted(data.get("items", []), key=lambda r: r.get("popularity", 0), reverse=True)
                for r in items:
                    add("tv" if r.get("media_type") == "tv" else "movie", r)
            elif kind == "tmdbCollection":
                data = tmdb.get(f"/collection/{q['collectionId']}")
                for r in sorted(data.get("parts", []), key=lambda r: r.get("popularity", 0), reverse=True):
                    add("movie", r)
            elif kind == "traktList" and trakt:
                for media, tid in trakt.list_items(q["username"], q["listSlug"])[:want * 2]:
                    try:
                        add(media, tmdb.get(f"/{media}/{tid}"))
                    except requests.HTTPError:
                        continue
                    if len(out) >= want:
                        break
        except requests.HTTPError as e:
            print(f"    source skipped ({kind}): {e}")
    if mode != "classic":
        out.sort(key=lambda t: t["popularity"], reverse=True)
    return out[:want]


def trakt_from_env():
    cid = os.environ.get("TRAKT_CLIENT_ID")
    return Trakt(cid) if cid else None
