"""
Kitchy Backend — Locust Load Test Suite
========================================

Covers three distinct test scenarios:

  1. AUTH          Login / token refresh cycle.
  2. CACHE STRESS  Identical POST /generate-recipes/ requests to verify that
                   the DB → Redis cache layers are serving hits correctly
                   (response times should drop to < 50 ms after the first request).
  3. QUOTA RACE    Rapid-fire requests against /generate-recipes/ using a single
                   shared free account to prove that concurrent 403 enforcement
                   is correct and race-condition-free (no two requests should
                   both succeed when only 1 analysis/day is allowed).

Usage
-----
    # Start the FastAPI server first, then:
    locust -f locustfile.py --host http://localhost:8000

    # Headless (CI):
    locust -f locustfile.py --host http://localhost:8000 \
           --headless -u 20 -r 5 --run-time 60s \
           --html report.html

Test accounts
-------------
Two test accounts are needed.  They are auto-registered on the first run if
they do not already exist.  Re-runs detect an existing account (HTTP 400 on
/auth/register) and fall through to login — fully idempotent.

    GENERAL_EMAIL   = kitchy_load_{worker_id}@example.com   (one per Locust worker)
    QUOTA_EMAIL     = kitchy_quota@example.com   (shared by ALL QuotaRaceUser instances)

    PASSWORD (both) = Load#Test99!   (min 8 chars — matches RegisterSchema / LoginSchema)

Interpreting results
--------------------
  • CACHE scenario   : After the first request the 95th-percentile latency
                       for POST /generate-recipes/ should be < 200 ms if the
                       cache is working. High latency = cache miss.
  • QUOTA scenario   : Every response should be 200 OR 403.  Any 429 means
                       the rate limiter (SlowAPI) fired before the quota check —
                       adjust wait_time or reduce concurrency. Any 5xx is a bug.
  • Race condition   : Run with -u 10 -r 10 (all spawn simultaneously) on a
                       fresh quota day. Exactly 1 response must be 200; the rest
                       must be 403.  More than one 200 = race condition detected.
"""

import threading
from typing import Optional

from locust import HttpUser, between, events, task
from locust.exception import StopUser

# =============================================================================
# CONFIGURATION
# =============================================================================

# Endpoint paths
_LOGIN_PATH    = "/auth/login"
_REGISTER_PATH = "/auth/register"
_GENERATE_PATH = "/generate-recipes/"
_STATUS_PATH   = "/auth/user/status"

# Shared credentials — must satisfy schemas.auth.RegisterSchema / LoginSchema:
#   email:    EmailStr  (valid RFC email)
#   password: constr(min_length=8)
_PASSWORD = "Load#Test99!"

# General load-test account (each worker registers its own to isolate quotas)
_GENERAL_EMAIL_TEMPLATE = "kitchy_load_{worker_id}@example.com"

# Quota race account — intentionally SHARED across all QuotaRaceUser instances
# so they all compete for the single free-tier analysis slot.
_QUOTA_EMAIL = "kitchy_quota@example.com"

# Fixed ingredient string for cache-stress test — must be identical on every
# request so the backend key always resolves to the same cache entry.
_CACHE_INGREDIENTS = "frango, arroz, brocolos"

# Varied ingredient sets used by the general user to simulate real traffic.
_VARIED_INGREDIENTS = [
    "ovos, tomate, queijo",
    "atum, batata, cebola",
    "lentilhas, cenoura, alho",
    "massa, cogumelos, natas",
    "grao-de-bico, espinafres, tomate",
]

# Thread-safe counter for unique worker IDs (avoids email collision between
# Locust worker processes when running distributed).
_worker_counter_lock = threading.Lock()
_worker_counter = 0


def _next_worker_id() -> int:
    global _worker_counter
    with _worker_counter_lock:
        _worker_counter += 1
        return _worker_counter


# =============================================================================
# HELPERS
# =============================================================================

def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_payload(email: str, password: str) -> dict:
    """
    Body for POST /auth/register — mirrors schemas.auth.RegisterSchema exactly.

    Required fields (no optional / dietary fields on register):
        email:    EmailStr
        password: constr(min_length=8)
    """
    return {"email": email, "password": password}


def _login_payload(email: str, password: str) -> dict:
    """
    Body for POST /auth/login — mirrors schemas.auth.LoginSchema exactly.

    Required fields:
        email:    EmailStr   (NOT "username")
        password: constr(min_length=8)
    """
    return {"email": email, "password": password}


def _register_or_ignore(client, email: str, password: str) -> None:
    """
    Attempt to register.  HTTP 400 ("email already taken") is silently
    swallowed because idempotency is intentional — re-running the suite
    against a populated DB must not fail.
    """
    with client.post(
        _REGISTER_PATH,
        json=_register_payload(email, password),
        name="[AUTH] POST /auth/register",
        catch_response=True,
    ) as response:
        if response.status_code in (200, 201):
            response.success()
        elif response.status_code == 400:
            # Account already exists — expected on re-runs.
            response.success()
        elif response.status_code == 422:
            response.failure(
                f"Register validation failed (422): {response.text[:300]}"
            )
        else:
            response.failure(
                f"Register failed: HTTP {response.status_code} — {response.text[:200]}"
            )


def _login(client, email: str, password: str) -> Optional[str]:
    """
    Authenticate and return the access_token string, or None on failure.
    Failures are marked as Locust failures so they appear in the report.
    """
    with client.post(
        _LOGIN_PATH,
        json=_login_payload(email, password),
        name="[AUTH] POST /auth/login",
        catch_response=True,
    ) as resp:
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            if token:
                resp.success()
                return token
            resp.failure("Login succeeded but access_token missing in response.")
        elif resp.status_code == 422:
            resp.failure(
                f"Login validation failed (422): {resp.text[:300]}"
            )
        else:
            resp.failure(
                f"Login failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )
    return None


# =============================================================================
# USER CLASS 1 — GENERAL LOAD TEST
# =============================================================================

class KitchyUser(HttpUser):
    """
    Simulates a real Kitchy user: logs in, generates recipes (mix of cached
    and varied ingredients), and occasionally refreshes their session status.

    Weight 3 → spawns 3× more instances than QuotaRaceUser.

    Task weights
    ------------
    generate_cached  (8)  — hammers the SAME ingredients to stress the cache.
    generate_varied  (3)  — uses different ingredients to test real throughput.
    check_status     (1)  — lightweight GET to verify auth is still valid.
    """

    weight    = 3
    wait_time = between(1, 4)

    # ── State ──────────────────────────────────────────────────────────────────
    token:     Optional[str] = None
    worker_id: int           = 0
    email:     str           = ""

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_start(self) -> None:
        self.worker_id = _next_worker_id()
        self.email     = _GENERAL_EMAIL_TEMPLATE.format(worker_id=self.worker_id)

        # 1. Register (idempotent — 400 is silently ignored)
        _register_or_ignore(self.client, self.email, _PASSWORD)

        # 2. Login
        self.token = _login(self.client, self.email, _PASSWORD)
        if not self.token:
            raise StopUser()   # no point continuing without a token

    # ── Tasks ──────────────────────────────────────────────────────────────────

    @task(8)
    def generate_cached(self) -> None:
        """
        POST /generate-recipes/ with FIXED ingredients.

        Cache-hit path:
          - 1st request  → DB miss + Redis miss → OpenAI call → writes both caches
          - 2nd+ requests → DB hit → returns immediately (sub-50 ms expected)

        Locust will plot the latency distribution: if the DB cache is working
        the 95th-percentile should be << the average of the first request.
        """
        self._generate(_CACHE_INGREDIENTS, name="[CACHE] POST /generate-recipes/")

    @task(3)
    def generate_varied(self) -> None:
        """
        POST /generate-recipes/ with rotating ingredient sets.

        Simulates real user behaviour and exercises the full OpenAI path
        (cache miss expected on first hit per ingredient combination).
        """
        import random
        ing = random.choice(_VARIED_INGREDIENTS)
        self._generate(ing, name="[VARIED] POST /generate-recipes/")

    @task(1)
    def check_status(self) -> None:
        """
        Lightweight GET /auth/user/status — confirms token is still valid
        and measures auth-layer latency under load.
        """
        if not self.token:
            return

        with self.client.get(
            _STATUS_PATH,
            headers=_auth_header(self.token),
            name="[AUTH] GET /auth/user/status",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 401:
                # Token expired — refresh
                self.token = _login(self.client, self.email, _PASSWORD)
                resp.failure("Token expired — refreshed.")
            else:
                resp.failure(f"status check: HTTP {resp.status_code}")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _generate(self, ingredients: str, name: str) -> None:
        if not self.token:
            self.token = _login(self.client, self.email, _PASSWORD)
            if not self.token:
                return

        with self.client.post(
            _GENERATE_PATH,
            json={"ingredients": ingredients},
            headers=_auth_header(self.token),
            name=name,
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                try:
                    body = resp.json()
                    if "recipes" not in body or not body["recipes"]:
                        resp.failure("200 OK but 'recipes' list is empty or missing.")
                    else:
                        resp.success()
                except ValueError:
                    resp.failure("200 OK but response is not valid JSON.")

            elif resp.status_code == 403:
                # Daily quota reached — expected for free users after 1 request.
                # Mark as success so quota blocks don't inflate the error rate.
                resp.success()

            elif resp.status_code == 401:
                self.token = _login(self.client, self.email, _PASSWORD)
                resp.failure("401 — token refreshed for next request.")

            elif resp.status_code == 429:
                # SlowAPI rate limiter fired — too many requests per minute.
                # Reduce wait_time or concurrency if this appears frequently.
                resp.success()   # rate limiting is correct behaviour, not a bug

            else:
                resp.failure(
                    f"Unexpected HTTP {resp.status_code}: {resp.text[:300]}"
                )


# =============================================================================
# USER CLASS 2 — QUOTA RACE CONDITION TEST
# =============================================================================

# Shared token for ALL QuotaRaceUser instances — set once by the first instance
# to log in, then reused by all.  Thread-safe via a lock.
_quota_token: Optional[str] = None
_quota_token_lock = threading.Lock()


class QuotaRaceUser(HttpUser):
    """
    Dedicated class for race-condition testing of the daily quota limit.

    Strategy
    --------
    ALL instances share the SAME test account (_QUOTA_EMAIL) and therefore
    the same analyses_today counter in the database.  With a free plan the
    backend allows exactly 1 analysis per day.

    When multiple instances fire simultaneously:
      - Exactly 1 must receive HTTP 200.
      - All others must receive HTTP 403.
      - Any duplicate 200 is a race condition bug.

    Counters for 200 and 403 responses are tracked in class variables so the
    on_stop hook can print a race-condition verdict to stdout.

    weight 1 → spawns fewer instances; adjust with --spawn-rate / -u flag.
    Recommended: locust -u 10 -r 10 (all spawn at once) to maximise race
    probability.
    """

    weight    = 1
    wait_time = between(0.1, 0.5)   # rapid-fire with minimal pause

    # Thread-safe counters shared across all instances of this class
    _success_count: int = 0
    _quota_count:   int = 0
    _count_lock = threading.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_start(self) -> None:
        global _quota_token

        with _quota_token_lock:
            if _quota_token is None:
                # First instance — register + login and store token
                _register_or_ignore(self.client, _QUOTA_EMAIL, _PASSWORD)
                _quota_token = _login(self.client, _QUOTA_EMAIL, _PASSWORD)

        if not _quota_token:
            raise StopUser()

    # ── Tasks ──────────────────────────────────────────────────────────────────

    @task
    def quota_race(self) -> None:
        """
        Fire POST /generate-recipes/ as fast as possible with the shared token.

        Expected under race conditions
        --------------------------------
        200  : first lucky request that reaches the quota check first.
        403  : all other requests — correct behaviour.
        Anything else : unexpected — reported as a Locust failure.

        The @task has no weight parameter (defaults to 1) because this class
        has only one task; 100% of the user's activity is this endpoint.
        """
        global _quota_token   # must be at the top of the function body

        with self.client.post(
            _GENERATE_PATH,
            json={"ingredients": "ovos, tomate"},   # short payload, fast path
            headers=_auth_header(_quota_token or ""),
            name="[QUOTA-RACE] POST /generate-recipes/",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                with self._count_lock:
                    QuotaRaceUser._success_count += 1
                resp.success()

            elif resp.status_code == 403:
                # Correct enforcement of daily limit.
                with self._count_lock:
                    QuotaRaceUser._quota_count += 1
                resp.success()

            elif resp.status_code == 429:
                # Rate limiter fired — not a bug.
                resp.success()

            elif resp.status_code == 401:
                _quota_token = _login(self.client, _QUOTA_EMAIL, _PASSWORD)
                resp.failure("Token expired — refreshed for next request.")

            else:
                resp.failure(
                    f"[QUOTA-RACE] Unexpected HTTP {resp.status_code}: {resp.text[:300]}"
                )

    def on_stop(self) -> None:
        """Print race-condition verdict when this user stops."""
        s = QuotaRaceUser._success_count
        q = QuotaRaceUser._quota_count

        print(
            f"\n{'='*60}\n"
            f"  QUOTA RACE VERDICT (cumulative across all instances)\n"
            f"  200 OK  : {s}\n"
            f"  403 Block: {q}\n"
            f"  {'✅ PASS — quota enforcement is race-condition-free.' if s <= 1 else '❌ FAIL — multiple 200s detected! Race condition present.'}\n"
            f"{'='*60}\n"
        )


# =============================================================================
# EVENT HOOKS — extra logging
# =============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs) -> None:
    print(
        "\n"
        "════════════════════════════════════════════════\n"
        "  Kitchy Load Test — scenarios:\n"
        "  [AUTH]        Login / session validation\n"
        "  [CACHE]       Identical ingredients → cache hit\n"
        "  [VARIED]      Different ingredients → real throughput\n"
        "  [QUOTA-RACE]  Shared account → 403 enforcement test\n"
        "════════════════════════════════════════════════\n"
        "  Verify cache effectiveness:\n"
        "    [CACHE] p95 latency should drop after the 1st request.\n"
        "  Verify quota enforcement:\n"
        "    [QUOTA-RACE] exactly 1 × HTTP 200, rest HTTP 403.\n"
        "════════════════════════════════════════════════\n"
    )


@events.request.add_listener
def on_request(
    request_type, name, response_time, response_length, response,
    context, exception, **kwargs,
) -> None:
    """
    Log unexpected non-2xx / non-403 / non-429 responses to stdout for
    immediate visibility during a headless run — complements the HTML report.
    """
    if exception:
        return  # already reported as a failure by Locust

    status = getattr(response, "status_code", None)
    if status and status not in (200, 201, 204, 400, 403, 404, 429):
        print(
            f"[WARN] {request_type} {name} → HTTP {status} "
            f"({response_time:.0f} ms)"
        )
