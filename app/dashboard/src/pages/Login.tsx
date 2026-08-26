import {
  Alert,
  AlertDescription,
  AlertIcon,
  Box,
  Button,
  chakra,
  Flex,
  FormControl,
  Heading,
  HStack,
  Icon,
  Text,
  useColorModeValue,
  VStack,
} from "@chakra-ui/react";
import {
  ArrowRightOnRectangleIcon,
  ServerStackIcon,
  ShieldCheckIcon,
} from "@heroicons/react/24/outline";
import { zodResolver } from "@hookform/resolvers/zod";
import { FC, ReactNode, useEffect, useState } from "react";
import { FieldValues, useForm } from "react-hook-form";
import { useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";
import { Footer } from "components/Footer";
import { Input } from "components/Input";
import { fetch } from "service/http";
import { removeAuthToken, setAuthToken } from "utils/authStorage";
import { ReactComponent as Logo } from "assets/logo.svg";
import { useTranslation } from "react-i18next";

const schema = z.object({
  username: z.string().min(1, "login.fieldRequired"),
  password: z.string().min(1, "login.fieldRequired"),
});

export const LogoIcon = chakra(Logo, {
  baseStyle: {
    strokeWidth: "10px",
    w: 12,
    h: 12,
  },
});

const LoginIcon = chakra(ArrowRightOnRectangleIcon, {
  baseStyle: {
    w: 5,
    h: 5,
    strokeWidth: "2px",
  },
});

type HighlightProps = {
  icon: typeof ShieldCheckIcon;
  title: string;
  hint: string;
};

const Highlight: FC<HighlightProps> = ({ icon, title, hint }) => (
  <HStack align="flex-start" spacing="3">
    <Flex
      align="center"
      justify="center"
      flexShrink={0}
      w="9"
      h="9"
      rounded="lg"
      bg="whiteAlpha.200"
    >
      <Icon as={icon} w="5" h="5" strokeWidth="2px" />
    </Flex>
    <Box>
      <Text fontWeight="semibold" fontSize="sm">
        {title}
      </Text>
      <Text fontSize="sm" color="whiteAlpha.700">
        {hint}
      </Text>
    </Box>
  </HStack>
);

/** Decorative blurred blob, purely visual. */
const Glow: FC<{ children?: ReactNode } & Record<string, any>> = (props) => (
  <Box
    position="absolute"
    rounded="full"
    filter="blur(80px)"
    pointerEvents="none"
    {...props}
  />
);

export const Login: FC = () => {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { t } = useTranslation();
  let location = useLocation();
  const {
    register,
    formState: { errors },
    handleSubmit,
  } = useForm({
    resolver: zodResolver(schema),
  });
  useEffect(() => {
    removeAuthToken();
    if (location.pathname !== "/login") {
      navigate("/login", { replace: true });
    }
  }, []);
  const login = (values: FieldValues) => {
    setError("");
    const formData = new FormData();
    formData.append("username", values.username);
    formData.append("password", values.password);
    formData.append("grant_type", "password");
    setLoading(true);
    fetch("/admin/token", { method: "post", body: formData })
      .then(({ access_token: token }) => {
        setAuthToken(token);
        navigate("/");
      })
      .catch((err) => {
        setError(err.response._data.detail);
      })
      .finally(setLoading.bind(null, false));
  };

  const formBg = useColorModeValue("white", "gray.800");
  const pageBg = useColorModeValue("gray.50", "gray.900");
  const borderColor = useColorModeValue("light-border", "gray.700");
  const mutedColor = useColorModeValue("gray.600", "gray.400");

  return (
    <Flex minH="100vh" w="full" bg={pageBg}>
      {/* Brand panel — decorative, so it is the first thing dropped on small screens. */}
      <Flex
        display={{ base: "none", lg: "flex" }}
        flex="1"
        direction="column"
        justify="space-between"
        position="relative"
        overflow="hidden"
        p="12"
        color="white"
        bgGradient="linear(to-br, primary.600, primary.900)"
      >
        <Glow top="-20" left="-16" w="80" h="80" bg="primary.300" opacity={0.45} />
        <Glow bottom="-24" right="-10" w="96" h="96" bg="primary.700" opacity={0.5} />

        <HStack spacing="3" position="relative">
          <LogoIcon w="9" h="9" />
          <Heading size="md" letterSpacing="tight">
            SkyPanel
          </Heading>
        </HStack>

        <VStack align="flex-start" spacing="8" position="relative" maxW="lg">
          <Heading size="lg" lineHeight="1.35" letterSpacing="tight">
            {t("login.tagline")}
          </Heading>
          <VStack align="stretch" spacing="5" w="full">
            <Highlight
              icon={ShieldCheckIcon}
              title={t("login.secureAccess")}
              hint={t("login.secureAccessHint")}
            />
            <Highlight
              icon={ServerStackIcon}
              title={t("login.manyServers")}
              hint={t("login.manyServersHint")}
            />
          </VStack>
        </VStack>

        <Box position="relative" />
      </Flex>

      {/* Form panel */}
      <Flex
        flex="1"
        direction="column"
        justify="space-between"
        align="center"
        p={{ base: "6", md: "10" }}
      >
        <Box />

        <Box
          w="full"
          maxW="380px"
          bg={formBg}
          borderWidth={{ base: "0", md: "1px" }}
          borderColor={borderColor}
          rounded="xl"
          shadow={{ base: "none", md: "sm" }}
          p={{ base: "0", md: "8" }}
        >
          <VStack align="flex-start" spacing="1" mb="6">
            <LogoIcon display={{ base: "block", lg: "none" }} mb="2" />
            <Heading size="lg" letterSpacing="tight">
              {t("login.loginYourAccount")}
            </Heading>
            <Text color={mutedColor} fontSize="sm">
              {t("login.welcomeBack")}
            </Text>
          </VStack>

          <form onSubmit={handleSubmit(login)}>
            <VStack rowGap={3} align="stretch">
              <FormControl>
                <Input
                  w="full"
                  placeholder={t("username")}
                  {...register("username")}
                  error={t(errors?.username?.message as string)}
                />
              </FormControl>
              <FormControl>
                <Input
                  w="full"
                  type="password"
                  placeholder={t("password")}
                  {...register("password")}
                  error={t(errors?.password?.message as string)}
                />
              </FormControl>
              {error && (
                <Alert status="error" rounded="md">
                  <AlertIcon />
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <Button
                isLoading={loading}
                type="submit"
                w="full"
                size="lg"
                fontSize="md"
                colorScheme="primary"
                mt="1"
              >
                <LoginIcon marginRight={2} />
                {t("login")}
              </Button>
            </VStack>
          </form>
        </Box>

        <Footer maxW="380px" />
      </Flex>
    </Flex>
  );
};

export default Login;
