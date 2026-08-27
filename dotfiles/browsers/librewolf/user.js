// LibreWolf user.js — deployed to ~/.librewolf/user.js
// Default search engine → SearXNG (self-hosted, localhost)
user_pref("keyword.URL", "http://127.0.0.1:8080/search?q=");

// Dark mode preference
user_pref("ui.systemUsesDarkTheme", 1);
user_pref("browser.theme.dark-private-windows", true);

// Enable userChrome.css (modular Zen-like minimalism)
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);

// Zen-like minimalism prefs
// Enable compact mode in Customize menu
user_pref("browser.compactmode.show", true);

// Native vertical tabs (Firefox 136+)
user_pref("sidebar.revamp", true);
user_pref("sidebar.verticalTabs", true);
user_pref("sidebar.visibility", "expand-on-hover");
user_pref("sidebar.position_start", true);

// Kill tab animations for instant feel
user_pref("browser.tabs.allowAnimationPreference", false);
user_pref("browser.tabs.animate", false);
