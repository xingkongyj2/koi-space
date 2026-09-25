/**
 * Barrel: side-effect-imports every adapter so they self-register, then
 * re-exports the public API for callers in the main process.
 */

// Adapters (side-effect register()):
import './python/adapter';

export { runEngine } from './runEngine';
export { get as getAdapter, DEFAULT_ENGINE_ID } from './registry';
export type {
  EngineAdapter,
  InstallProbe,
  RunEngineOptions,
} from './types';
