const token = process.env.EXPO_PUBLIC_LOCAL_AUTH_TOKEN?.trim();

export const localAuthToken = token?.startsWith("mul_") ? token : null;
