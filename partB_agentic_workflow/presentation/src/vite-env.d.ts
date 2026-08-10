/// <reference types="vite/client" />

declare const __PRESENTATION_EVIDENCE__: {
  schemaVersion: number;
  sourceCommitSha: string;
  generatedAt: string;
  mode: string;
  source: string;
  status: string;
  toolAllowlist: string[];
  claims: {
    supportedLanguages: string[];
    supportedAttachments: string[];
    coreScenes: number;
    promotionSampleFloor: number;
  };
};
