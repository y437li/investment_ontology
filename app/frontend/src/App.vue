<template>
  <router-view />
</template>

<script setup>
// Vue Router manages all page navigation
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

#app {
  font-family: 'Space Grotesk', 'Inter', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: #000000;
  background-color: #ffffff;
}

::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: #f1f1f1;
}

::-webkit-scrollbar-thumb {
  background: #000000;
}

::-webkit-scrollbar-thumb:hover {
  background: #333333;
}

button {
  font-family: inherit;
}

:root {
  --black: #000000;
  --white: #FFFFFF;
  --accent: #1a56db;
  --accent-light: #e8f0fe;
  --gray-light: #F5F5F5;
  --gray-text: #666666;
  --border: #E5E5E5;
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', 'Inter', system-ui, sans-serif;
}
</style>
