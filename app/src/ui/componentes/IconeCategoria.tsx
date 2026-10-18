import type { ComponentType } from "react";
import { View } from "react-native";

import { useTema } from "@/ui/tema";

/**
 * O ícone Phosphor de uma categoria. O servidor deriva `icone` do emoji (`db/cat_icones.py`, que o painel web também usa) e este
 * registro tem os MESMOS nomes: `__tests__/ui/icone_categoria.test.tsx` lê o `.py` e reprova se um faltar. Nome desconhecido
 * (servidor novo, app velho) cai em `tag`, como o `ICONE_NEUTRO` do servidor. Lista própria, não o `NomeIcone` do `Icone`.
 * Decorativo: o nome da categoria é quem fala. `require` por ícone pelo mesmo motivo do `Icone` (Metro só resolve caminho estático).
 */
type ComponenteIcone = ComponentType<{ size?: number; color?: string; weight?: string }>;
/** Nomeado (`<Nome>Icon`) em produção, `default` no stub do Jest: o mesmo `resolver` do `Icone`, local porque os testes que mockam o `Icone` o apagariam. */
const resolver = (mod: unknown, nomeado: string) => { const m = mod as Record<string, ComponenteIcone | undefined>; return (m[nomeado] ?? m.default)!; };

const ICONES = {
  "airplane-tilt": resolver(require("phosphor-react-native/src/icons/AirplaneTilt"), "AirplaneTiltIcon"),
  "apple-logo": resolver(require("phosphor-react-native/src/icons/AppleLogo"), "AppleLogoIcon"),
  "baby": resolver(require("phosphor-react-native/src/icons/Baby"), "BabyIcon"),
  "bank": resolver(require("phosphor-react-native/src/icons/Bank"), "BankIcon"),
  "barbell": resolver(require("phosphor-react-native/src/icons/Barbell"), "BarbellIcon"),
  "basket": resolver(require("phosphor-react-native/src/icons/Basket"), "BasketIcon"),
  "bed": resolver(require("phosphor-react-native/src/icons/Bed"), "BedIcon"),
  "beer-stein": resolver(require("phosphor-react-native/src/icons/BeerStein"), "BeerSteinIcon"),
  "book-open": resolver(require("phosphor-react-native/src/icons/BookOpen"), "BookOpenIcon"),
  "books": resolver(require("phosphor-react-native/src/icons/Books"), "BooksIcon"),
  "bread": resolver(require("phosphor-react-native/src/icons/Bread"), "BreadIcon"),
  "briefcase": resolver(require("phosphor-react-native/src/icons/Briefcase"), "BriefcaseIcon"),
  "broadcast": resolver(require("phosphor-react-native/src/icons/Broadcast"), "BroadcastIcon"),
  "broom": resolver(require("phosphor-react-native/src/icons/Broom"), "BroomIcon"),
  "buildings": resolver(require("phosphor-react-native/src/icons/Buildings"), "BuildingsIcon"),
  "bus": resolver(require("phosphor-react-native/src/icons/Bus"), "BusIcon"),
  "cake": resolver(require("phosphor-react-native/src/icons/Cake"), "CakeIcon"),
  "calendar-dots": resolver(require("phosphor-react-native/src/icons/CalendarDots"), "CalendarDotsIcon"),
  "camera": resolver(require("phosphor-react-native/src/icons/Camera"), "CameraIcon"),
  "car": resolver(require("phosphor-react-native/src/icons/Car"), "CarIcon"),
  "chart-bar": resolver(require("phosphor-react-native/src/icons/ChartBar"), "ChartBarIcon"),
  "church": resolver(require("phosphor-react-native/src/icons/Church"), "ChurchIcon"),
  "cloud": resolver(require("phosphor-react-native/src/icons/Cloud"), "CloudIcon"),
  "coffee": resolver(require("phosphor-react-native/src/icons/Coffee"), "CoffeeIcon"),
  "coins": resolver(require("phosphor-react-native/src/icons/Coins"), "CoinsIcon"),
  "credit-card": resolver(require("phosphor-react-native/src/icons/CreditCard"), "CreditCardIcon"),
  "currency-btc": resolver(require("phosphor-react-native/src/icons/CurrencyBtc"), "CurrencyBtcIcon"),
  "desktop": resolver(require("phosphor-react-native/src/icons/Desktop"), "DesktopIcon"),
  "device-mobile": resolver(require("phosphor-react-native/src/icons/DeviceMobile"), "DeviceMobileIcon"),
  "diamond": resolver(require("phosphor-react-native/src/icons/Diamond"), "DiamondIcon"),
  "dog": resolver(require("phosphor-react-native/src/icons/Dog"), "DogIcon"),
  "drop": resolver(require("phosphor-react-native/src/icons/Drop"), "DropIcon"),
  "file-text": resolver(require("phosphor-react-native/src/icons/FileText"), "FileTextIcon"),
  "film-slate": resolver(require("phosphor-react-native/src/icons/FilmSlate"), "FilmSlateIcon"),
  "first-aid": resolver(require("phosphor-react-native/src/icons/FirstAid"), "FirstAidIcon"),
  "flame": resolver(require("phosphor-react-native/src/icons/Flame"), "FlameIcon"),
  "flower": resolver(require("phosphor-react-native/src/icons/Flower"), "FlowerIcon"),
  "fork-knife": resolver(require("phosphor-react-native/src/icons/ForkKnife"), "ForkKnifeIcon"),
  "game-controller": resolver(require("phosphor-react-native/src/icons/GameController"), "GameControllerIcon"),
  "gas-pump": resolver(require("phosphor-react-native/src/icons/GasPump"), "GasPumpIcon"),
  "gift": resolver(require("phosphor-react-native/src/icons/Gift"), "GiftIcon"),
  "globe": resolver(require("phosphor-react-native/src/icons/Globe"), "GlobeIcon"),
  "graduation-cap": resolver(require("phosphor-react-native/src/icons/GraduationCap"), "GraduationCapIcon"),
  "guitar": resolver(require("phosphor-react-native/src/icons/Guitar"), "GuitarIcon"),
  "hamburger": resolver(require("phosphor-react-native/src/icons/Hamburger"), "HamburgerIcon"),
  "handbag": resolver(require("phosphor-react-native/src/icons/Handbag"), "HandbagIcon"),
  "handshake": resolver(require("phosphor-react-native/src/icons/Handshake"), "HandshakeIcon"),
  "heart": resolver(require("phosphor-react-native/src/icons/Heart"), "HeartIcon"),
  "heart-straight": resolver(require("phosphor-react-native/src/icons/HeartStraight"), "HeartStraightIcon"),
  "house": resolver(require("phosphor-react-native/src/icons/House"), "HouseIcon"),
  "laptop": resolver(require("phosphor-react-native/src/icons/Laptop"), "LaptopIcon"),
  "lifebuoy": resolver(require("phosphor-react-native/src/icons/Lifebuoy"), "LifebuoyIcon"),
  "lightbulb": resolver(require("phosphor-react-native/src/icons/Lightbulb"), "LightbulbIcon"),
  "magnifying-glass": resolver(require("phosphor-react-native/src/icons/MagnifyingGlass"), "MagnifyingGlassIcon"),
  "mask-happy": resolver(require("phosphor-react-native/src/icons/MaskHappy"), "MaskHappyIcon"),
  "microphone": resolver(require("phosphor-react-native/src/icons/Microphone"), "MicrophoneIcon"),
  "music-notes": resolver(require("phosphor-react-native/src/icons/MusicNotes"), "MusicNotesIcon"),
  "paint-brush": resolver(require("phosphor-react-native/src/icons/PaintBrush"), "PaintBrushIcon"),
  "paw-print": resolver(require("phosphor-react-native/src/icons/PawPrint"), "PawPrintIcon"),
  "piggy-bank": resolver(require("phosphor-react-native/src/icons/PiggyBank"), "PiggyBankIcon"),
  "pill": resolver(require("phosphor-react-native/src/icons/Pill"), "PillIcon"),
  "pizza": resolver(require("phosphor-react-native/src/icons/Pizza"), "PizzaIcon"),
  "printer": resolver(require("phosphor-react-native/src/icons/Printer"), "PrinterIcon"),
  "receipt": resolver(require("phosphor-react-native/src/icons/Receipt"), "ReceiptIcon"),
  "scissors": resolver(require("phosphor-react-native/src/icons/Scissors"), "ScissorsIcon"),
  "shield": resolver(require("phosphor-react-native/src/icons/Shield"), "ShieldIcon"),
  "shopping-cart": resolver(require("phosphor-react-native/src/icons/ShoppingCart"), "ShoppingCartIcon"),
  "sneaker-move": resolver(require("phosphor-react-native/src/icons/SneakerMove"), "SneakerMoveIcon"),
  "spray-bottle": resolver(require("phosphor-react-native/src/icons/SprayBottle"), "SprayBottleIcon"),
  "stethoscope": resolver(require("phosphor-react-native/src/icons/Stethoscope"), "StethoscopeIcon"),
  "t-shirt": resolver(require("phosphor-react-native/src/icons/TShirt"), "TShirtIcon"),
  "tag": resolver(require("phosphor-react-native/src/icons/Tag"), "TagIcon"),
  "target": resolver(require("phosphor-react-native/src/icons/Target"), "TargetIcon"),
  "taxi": resolver(require("phosphor-react-native/src/icons/Taxi"), "TaxiIcon"),
  "television": resolver(require("phosphor-react-native/src/icons/Television"), "TelevisionIcon"),
  "ticket": resolver(require("phosphor-react-native/src/icons/Ticket"), "TicketIcon"),
  "tooth": resolver(require("phosphor-react-native/src/icons/Tooth"), "ToothIcon"),
  "train": resolver(require("phosphor-react-native/src/icons/Train"), "TrainIcon"),
  "tree": resolver(require("phosphor-react-native/src/icons/Tree"), "TreeIcon"),
  "tree-evergreen": resolver(require("phosphor-react-native/src/icons/TreeEvergreen"), "TreeEvergreenIcon"),
  "trend-down": resolver(require("phosphor-react-native/src/icons/TrendDown"), "TrendDownIcon"),
  "trend-up": resolver(require("phosphor-react-native/src/icons/TrendUp"), "TrendUpIcon"),
  "umbrella": resolver(require("phosphor-react-native/src/icons/Umbrella"), "UmbrellaIcon"),
  "wine": resolver(require("phosphor-react-native/src/icons/Wine"), "WineIcon"),
  "wrench": resolver(require("phosphor-react-native/src/icons/Wrench"), "WrenchIcon"),
} as const;

const NEUTRO = "tag";

export function IconeCategoria({ icone, tamanho = 20, cor }: { icone: string; tamanho?: number; cor?: string }) {
  const { cores } = useTema();
  const nome = Object.hasOwn(ICONES, icone) ? icone : NEUTRO;
  const Componente = ICONES[nome as keyof typeof ICONES];
  return (
    <View testID={`icone-categoria-${nome}`} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <Componente size={tamanho} color={cor ?? cores.inkMuted} weight="regular" />
    </View>
  );
}

/** Os nomes do registro, para o teste de paridade com o servidor. */
export const NOMES_ICONES_CATEGORIA = Object.keys(ICONES);
