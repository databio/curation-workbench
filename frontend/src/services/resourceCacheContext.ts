import { createContext } from 'react';
import type { ResourceCache } from './resourceCache';

export const ResourceCacheContext = createContext<ResourceCache | null>(null);
