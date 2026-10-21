// Every page URL in one place; all are project-scoped (/p/:project/...).
export const projectPath = (p: string) => `/p/${encodeURIComponent(p)}`;
export const typePath = (p: string, track: string, type: string) =>
  `${projectPath(p)}/review/${encodeURIComponent(track)}/${encodeURIComponent(type)}`;
export const subjectPath = (p: string, slug: string) => `${projectPath(p)}/subject/${encodeURIComponent(slug)}`;
export const approvedPath = (p: string) => `${projectPath(p)}/approved`;
export const morePath = (p: string) => `${projectPath(p)}/more`;
export const historyPath = (p: string) => `${projectPath(p)}/history`;
export const allPath = (p: string) => `${projectPath(p)}/all`;
export const findingsPath = (p: string) => `${projectPath(p)}/findings`;
export const teamPath = (p: string) => `${projectPath(p)}/team`;
