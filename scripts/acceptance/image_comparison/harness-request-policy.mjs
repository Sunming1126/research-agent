// Local testing adapter: preserve the target's output-token limit in Harness.
// No file/shell/network tools are added, and no credentials are accessed here.
export const name = 'acceptance-request-policy';
export const inject = ['agents'];
export function apply(ctx) {
  const maximum = Number(process.env.TEST_HARNESS_MAX_TOKENS || '0');
  if (!Number.isInteger(maximum) || maximum < 1) return;
  // The headless app supplies provider/model through AgentOptions. Its
  // per-agent dispatcher does not inherit a sibling's request event handler,
  // so add the supported maxTokens option at agent creation in this process.
  const agents = ctx.get('agents');
  const original = agents.create;
  ctx.effect(() => {
    agents.create = function (request) {
      return Reflect.apply(original, this, [{
        ...request,
        agentOptions: { ...request.agentOptions, maxTokens: maximum },
      }]);
    };
    return () => { agents.create = original; };
  });
  ctx.provide('acceptanceRequestPolicy', { maxTokens: maximum });
}
