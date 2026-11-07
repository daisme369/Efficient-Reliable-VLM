import { createHash, createHmac, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type {
  AIRequest, AuditEvent, BoxDTO, FindingsDTO, MessageDTO, MutationReceipt, ReportDTO,
  SessionDTO, UserDTO,
} from '../contracts';
import { patients } from './fixtures';

export class MockError extends Error {
  constructor(readonly status: number, readonly code: string, message: string, readonly retryAfter?: number) {
    super(message);
  }
}

export function fail(status: number, code: string, message: string): never {
  throw new MockError(status, code, message);
}

export function invalid(message: string): never {
  return fail(422, 'VALIDATION_ERROR', message);
}

export function missing(): never {
  return fail(404, 'RESOURCE_NOT_FOUND', 'This resource is unavailable.');
}

export const isoNow = () => new Date().toISOString();
export const hash = (input: string | Buffer) => createHash('sha256').update(input).digest('hex');
export const idleMs = 30 * 60 * 1000;

export interface MockUser extends UserDTO {
  password_hash: string;
}

export interface MockSession {
  session: SessionDTO;
  findings: FindingsDTO[];
  reports: ReportDTO[];
  boxes: BoxDTO[];
  messages: MessageDTO[];
  jobs: MockJob[];
  events: AuditEvent[];
  analyzedImageId: number | null;
}

export interface MockJob {
  request: AIRequest;
  context: string;
  submittedAt: number;
  failure?: 'timeout' | 'detector';
}

interface LoginSession {
  userId: number;
  expires: number;
}

interface IdempotencyReceipt {
  bodyHash: string;
  status: number;
  data: MutationReceipt | AIRequest;
}

export interface MockState {
  sequence: number;
  secret: string;
  users: MockUser[];
  sessions: MockSession[];
  assignments: { id: number; doctor_id: number; patient_id: number; active: boolean }[];
  logins: Record<string, LoginSession>;
  receipts: Record<string, IdempotencyReceipt>;
  failedLogins: Record<string, number[]>;
}

function statePath() {
  return process.env.THORAX_DEMO_STATE_PATH || join(process.cwd(), '.thorax-demo', 'state.json');
}

function freshState(): MockState {
  return {
    sequence: 10000,
    secret: randomBytes(32).toString('hex'),
    users: [],
    sessions: [],
    assignments: patients.flatMap(({ id }) => {
      const ids = id <= 10 ? [1] : [2];
      if (id === 1) ids.push(2);
      return ids.map((doctor_id) => ({
        id: doctor_id * 100 + id, doctor_id, patient_id: id, active: true,
      }));
    }),
    logins: {},
    receipts: {},
    failedLogins: {},
  };
}

function seedUsers(state: MockState) {
  const seeds = [
    { id: 1, name: 'Dr. Maya Bennett', email: 'doctor@thorax.demo', role: 'doctor', password: process.env.THORAX_DEMO_DOCTOR_PASSWORD },
    { id: 2, name: 'Dr. Leo Martinez', email: 'doctor2@thorax.demo', role: 'doctor', password: process.env.THORAX_DEMO_DOCTOR_PASSWORD },
    { id: 3, name: 'Demo administrator', email: 'admin@thorax.demo', role: 'admin', password: process.env.THORAX_DEMO_ADMIN_PASSWORD },
  ] as const;
  for (const { password, ...user } of seeds) {
    if (!password || !validPassword(password, 12) || state.users.some(({ id }) => id === user.id)) continue;
    state.users.push({ ...user, is_active: true, password_hash: hashPassword(password) });
  }
}

export function readState() {
  let state: MockState;
  try {
    state = JSON.parse(readFileSync(statePath(), 'utf8')) as MockState;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    state = freshState();
  }
  const previous = JSON.stringify(state);
  seedUsers(state);
  return { state, previous };
}

export function saveState(state: MockState, previous: string) {
  const serialized = JSON.stringify(state);
  if (serialized === previous) return;
  const path = statePath();
  mkdirSync(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, serialized, { mode: 0o600 });
  renameSync(temporary, path);
}

export function validPassword(password: string, minimum: number) {
  const bytes = Buffer.byteLength(password, 'utf8');
  return bytes >= minimum && bytes <= 72;
}

export function hashPassword(password: string) {
  const salt = randomBytes(16).toString('hex');
  return `${salt}:${scryptSync(password, salt, 64).toString('hex')}`;
}

const dummyPasswordHash = `${'0'.repeat(32)}:${'0'.repeat(128)}`;

export function passwordMatches(password: string, user?: MockUser) {
  const [salt, expected] = (user?.password_hash ?? dummyPasswordHash).split(':');
  return timingSafeEqual(scryptSync(password, salt, 64), Buffer.from(expected, 'hex'));
}

export function publicUser({ id, name, email, role, is_active }: MockUser): UserDTO {
  return { id, name, email, role, is_active };
}

export function assigned(state: MockState, doctorId: number, patientId: number) {
  return state.assignments.some((row) => row.doctor_id === doctorId && row.patient_id === patientId && row.active);
}

export function nextId(state: MockState) {
  return ++state.sequence;
}

export function event(state: MockState, reading: MockSession, type: string, data: Record<string, unknown>) {
  reading.events.push({ id: nextId(state), type, created_at: isoNow(), user_id: reading.session.doctor_id, data });
}

export function keyedHash(state: MockState, value: string) {
  return createHmac('sha256', state.secret).update(value).digest('hex');
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    const items = Object.entries(value).sort(([a], [b]) => a < b ? -1 : Number(a > b));
    return `{${items.map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

export function idempotent(
  state: MockState,
  request: Request,
  actor: MockUser,
  body: unknown,
  required: boolean,
  mutate: () => { status: number; data: MutationReceipt | AIRequest },
) {
  const key = request.headers.get('Idempotency-Key');
  if (!key && !required) return mutate();
  if (!key || !/^[ -~]{16,128}$/.test(key)) invalid('A valid action key is required.');

  const scope = keyedHash(state, `${actor.id}:${request.method}:${new URL(request.url).pathname}:${key}`);
  const bodyHash = keyedHash(state, canonical(body));
  const receipt = state.receipts[scope];
  if (receipt) {
    if (receipt.bodyHash !== bodyHash) {
      fail(409, 'IDEMPOTENCY_CONFLICT', 'This action key was already used with different input.');
    }
    return receipt;
  }
  const result = mutate();
  state.receipts[scope] = { ...structuredClone(result), bodyHash };
  return result;
}
