/**
 * Pings the backend's /api/cron/tick on a schedule.
 *
 * This runs on Cloudflare's free tier — it isn't running any of the
 * project's Python logic, just acting as the clock for a backend that has
 * no persistent worker process of its own to do that internally.
 */
export default {
  async scheduled(event, env, ctx) {
    const res = await fetch(`${env.BACKEND_URL}/api/cron/tick`, {
      method: "POST",
      headers: { "X-Cron-Secret": env.CRON_SECRET },
    });
    console.log(`cron tick: ${res.status} ${await res.text()}`);
  },
};
