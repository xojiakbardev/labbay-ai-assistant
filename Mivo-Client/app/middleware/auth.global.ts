export default defineNuxtRouteMiddleware(async (to) => {
  const publicPaths = ["/login", "/privacy"];
  if (publicPaths.includes(to.path)) return;

  const { isAuthenticated, isSuperadmin, fetchMe } = useAuth();
  if (!isAuthenticated.value) {
    return navigateTo("/login");
  }

  // Role isn't known yet on a fresh page load (state resets — ssr:false, no
  // server session) — resolve it once before deciding where this user
  // belongs, so a hard refresh on a superadmin route doesn't briefly bounce
  // them to the business dashboard.
  if (isSuperadmin.value === null) {
    await fetchMe();
  }

  const isSuperadminRoute = to.path.startsWith("/superadmin");
  if (isSuperadmin.value && !isSuperadminRoute) {
    return navigateTo("/superadmin");
  }
  if (!isSuperadmin.value && isSuperadminRoute) {
    return navigateTo("/");
  }
});
