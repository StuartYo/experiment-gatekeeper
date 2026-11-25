import flagsmith from '@flagsmith/flagsmith';

await flagsmith.init({
  environmentID: import.meta.env.VITE_FLAGSMITH_KEY,
  enableEvents: true,
  defaultFlags: { hero_copy: { enabled: true, value: 'control' } },
});

const visitor = crypto.randomUUID();
const flag = flagsmith.getExperimentFlag('hero_copy');
await flagsmith.identify(visitor);
document.querySelector('h1').textContent = flag?.value ?? 'Plans that scale';

document.querySelector('#buy').addEventListener('click', () => {
  flagsmith.trackEvent('checkout');
});
