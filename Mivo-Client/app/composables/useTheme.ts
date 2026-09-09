export function useTheme() {
  const theme = ref<"dark" | "light">("dark");

  onMounted(() => {
    const saved = localStorage.getItem("mivo_theme") as "dark" | "light" | null;
    if (saved) {
      theme.value = saved;
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      theme.value = "light";
    }
    applyTheme(theme.value);
  });

  function applyTheme(newTheme: "dark" | "light") {
    theme.value = newTheme;
    localStorage.setItem("mivo_theme", newTheme);
    if (import.meta.client) {
      document.documentElement.setAttribute("data-theme", newTheme);
    }
  }

  function toggleTheme() {
    const next = theme.value === "dark" ? "light" : "dark";
    applyTheme(next);
  }

  return {
    theme,
    toggleTheme,
    applyTheme,
  };
}
