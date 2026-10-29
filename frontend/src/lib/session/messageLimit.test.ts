import { describe, expect, it } from 'vitest';

import { MAX_MESSAGE_CHARS, messageTooLong } from './messageLimit';

describe('messageTooLong', () => {
  it('lets a message at the limit through and explains one over it', () => {
    expect(messageTooLong('x'.repeat(MAX_MESSAGE_CHARS))).toBeNull();
    expect(messageTooLong('x'.repeat(MAX_MESSAGE_CHARS + 1))).toMatch(/Attach long content as a file/);
  });
});
