import flagsmith from '@flagsmith/flagsmith';

await flagsmith.init({
  environmentID: import.meta.env.VITE_FLAGSMITH_KEY,
  defaultFlags: { hero_copy: { enabled: true, value: 'control' } },
  api: 'https://flags.internal.example/api/v1/',
});

const visitor = localStorage.getItem('vid') ?? crypto.randomUUID();
localStorage.setItem('vid', visitor);
await flagsmith.identify(visitor);
const flag = flagsmith.getExperimentFlag('hero_copy');
document.querySelector('h1').textContent = flag?.value ?? 'Plans that scale';

document.querySelector('#buy').addEventListener('click', () => {
  flagsmith.trackEvent('checkout');
});
