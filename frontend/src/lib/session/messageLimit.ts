/** The longest message a session takes (the supervisor's limit, 64 Ki characters): a message
 * is stored in the session's workflow history, which refuses anything much bigger. */
export const MAX_MESSAGE_CHARS = 64 * 1024;

/** Why `text` cannot be sent, or null when it can. */
export function messageTooLong(text: string): string | null {
  if (text.length <= MAX_MESSAGE_CHARS) return null;
  return `This message is ${text.length.toLocaleString()} characters; a session takes at most ${MAX_MESSAGE_CHARS.toLocaleString()}. Attach long content as a file instead.`;
}
