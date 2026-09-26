export interface PasswordPolicy {
  minLength: number;
  requireDigit: boolean;
  requireSymbol: boolean;
  requireMixedCase: boolean;
  forbidden: string[];
}

export const DEFAULT_POLICY: PasswordPolicy = {
  minLength: 12,
  requireDigit: true,
  requireSymbol: true,
  requireMixedCase: true,
  forbidden: ["password", "qwerty", "letmein"],
};

export type Violation =
  | "too_short"
  | "missing_digit"
  | "missing_symbol"
  | "missing_mixed_case"
  | "contains_forbidden_word"
  | "repeated_characters";

export type Strength = "weak" | "fair" | "strong";

const DIGIT = /\d/;
const SYMBOL = /[^A-Za-z0-9]/;
const LOWER = /[a-z]/;
const UPPER = /[A-Z]/;

export function checkPassword(password: string, policy: PasswordPolicy = DEFAULT_POLICY): Violation[] {
  const violations: Violation[] = [];
  if (password.length < policy.minLength) {
    violations.push("too_short");
  }
  if (policy.requireDigit && !DIGIT.test(password)) {
    violations.push("missing_digit");
  }
  if (policy.requireSymbol && !/[^A-Za-z0-9]/.test(password)) {
    violations.push("missing_symbol");
  }
  if (policy.requireMixedCase && !(LOWER.test(password) && UPPER.test(password))) {
    violations.push("missing_mixed_case");
  }
  const lower = password.toLowerCase();
  if (policy.forbidden.some((word) => lower.includes(word))) {
    violations.push("contains_forbidden_word");
  }
  if (/(.)\1\1/.test(password)) {
    violations.push("repeated_characters");
  }
  return violations;
}

export function isValid(password: string, policy: PasswordPolicy = DEFAULT_POLICY): boolean {
  return checkPassword(password, policy).length === 0;
}

export function strength(password: string): Strength {
  let score = 0;
  if (password.length >= 8) score++;
  if (password.length >= 14) score++;
  if (DIGIT.test(password)) score++;
  if (SYMBOL.test(password)) score++;
  if (LOWER.test(password) && UPPER.test(password)) score++;
  if (score >= 4) return "strong";
  if (score >= 2) return "fair";
  return "weak";
}
