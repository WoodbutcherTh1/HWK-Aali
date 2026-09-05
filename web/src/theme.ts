/* Shared framer-motion presets — the "motion" language of Aali. */

export const spring = { type: "spring", stiffness: 380, damping: 30 } as const;
export const softSpring = { type: "spring", stiffness: 220, damping: 26 } as const;

export const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.1 } },
};

export const riseIn = {
  hidden: { opacity: 0, y: 14, scale: 0.98 },
  show: { opacity: 1, y: 0, scale: 1, transition: softSpring },
};

export const msgIn = {
  initial: { opacity: 0, y: 10, scale: 0.99 },
  animate: { opacity: 1, y: 0, scale: 1, transition: softSpring },
};

export const orbPulse = {
  scale: [1, 1.06, 1],
  transition: { duration: 3.2, repeat: Infinity, ease: "easeInOut" as const },
};
