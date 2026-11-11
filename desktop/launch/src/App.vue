<script setup>
import iconUrl from '../../icons/128x128@2x.png';
import { useLaunch } from './useLaunch';

const { phase, leaving, errorMessage, srStatus, retry } = useLaunch();
</script>

<template>
  <div class="launch" :class="{ 'is-error': phase === 'error', 'is-leaving': leaving }">
    <div class="scene" aria-hidden="true">
      <span class="aurora aurora-a"></span>
      <span class="aurora aurora-b"></span>
      <span class="aurora aurora-c"></span>
      <span class="floor-grid"></span>
      <span class="ring ring-a"></span>
      <span class="ring ring-b"></span>
    </div>

    <main class="hero">
      <div class="icon-stage">
        <span class="icon-halo" aria-hidden="true"></span>
        <span class="orbit orbit-a" aria-hidden="true"></span>
        <span class="orbit orbit-b" aria-hidden="true"></span>

        <div class="tile-float">
          <div class="icon-tile">
            <span class="tile-sheen" aria-hidden="true"></span>
            <span class="tile-scan" aria-hidden="true"></span>
            <div class="icon-plate">
              <img class="icon-art" :src="iconUrl" alt="" draggable="false" />
            </div>
          </div>
        </div>

        <span class="spark spark-a" aria-hidden="true"></span>
        <span class="spark spark-b" aria-hidden="true"></span>
        <span class="spark spark-c" aria-hidden="true"></span>
      </div>

      <h1 class="wordmark">Pygmalion <span>Studio</span></h1>

      <button
        v-if="phase === 'error'"
        class="retry"
        type="button"
        aria-label="重新检测本地服务"
        :title="errorMessage"
        @click="retry"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20 11a8.1 8.1 0 0 0-14.9-4.5L4 8" />
          <path d="M4 4v4h4" />
          <path d="M4 13a8.1 8.1 0 0 0 14.9 4.5L20 16" />
          <path d="M20 20v-4h-4" />
        </svg>
      </button>

      <p class="sr-only" aria-live="polite">{{ srStatus }}</p>
    </main>
  </div>
</template>
